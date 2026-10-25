from __future__ import annotations

import csv
import json
import math
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_feedback.engine import (
    configure_control_group,
    evaluate_shadow,
    unavailable_decision,
)
from half_linac.src.apps.energy_feedback.client import EpicsPVClient
from half_linac.src.apps.energy_feedback.csv_logger import EnergyFeedbackCsvLogger
from half_linac.src.apps.energy_feedback.measurement import DirectEnergyProvider
from half_linac.src.apps.energy_feedback.models import (
    ActuatorCommand,
    EnergyMeasurement,
    FeedbackDecision,
    MeasurementQuality,
    PVReading,
    ShadowActuator,
    ShadowControlGroup,
)
from half_linac.src.apps.energy_feedback.profile_runtime import load_runtime_config
from half_linac.src.apps.energy_feedback.safety import (
    FeedbackSafetyStateMachine,
)
from half_linac.src.apps.energy_feedback.writer import (
    apply_feedback_decision,
    apply_manual_targets,
)
from half_linac.src.shared.machine_profile import (
    AppContext,
    ControlBackendConfig,
    ElementConfig,
    MachineConfig,
    MachineProfile,
    MachineProfileError,
    REAL_STATUS_WRITE_SMOKE_PASSED,
    describe_app_support,
    load_app_context,
    real_commissioning_status,
    resolve_energy_feedback_workflow,
    validate_energy_feedback_workflow,
)


class FakeClient:
    def __init__(self, readings):
        self.readings = dict(readings)
        self.requests = []

    def read_many(self, keys):
        keys = tuple(keys)
        self.requests.append(keys)
        return {key: self.readings[key] for key in keys}


class FakeWritableClient(FakeClient):
    def __init__(self, readings, links, *, fail_once=()):
        super().__init__(readings)
        self.links = dict(links)
        self.fail_once = set(fail_once)
        self.writes = []

    def write(self, key, value, *, wait=True, timeout_s=1.0):
        self.writes.append((key, float(value)))
        if key in self.fail_once:
            self.fail_once.remove(key)
            return False
        readback_key = self.links[key]
        previous = self.readings[readback_key]
        self.readings[readback_key] = reading(
            readback_key,
            float(value),
            timestamp=previous.source_timestamp,
        )
        return True


def reading(
    key: str,
    value: float | None,
    *,
    timestamp: float | None = 100.0,
    connected: bool = True,
    error: str = "",
) -> PVReading:
    return PVReading(key, f"DEMO:{key}", value, connected, timestamp, error)


def measurement(error_mev: float = 4.0) -> EnergyMeasurement:
    return EnergyMeasurement(
        sampled_at=100.0,
        source_timestamp=100.0,
        target_source_timestamp=100.0,
        energy_mev=104.0,
        target_mev=100.0,
        error_mev=error_mev,
        diagnostics={},
        diagnostic_errors={},
        quality=MeasurementQuality("OK", "measurement valid", True),
    )


def actuator(
    actuator_id: str,
    *,
    coefficient: float = 1.0,
    max_step: float = 10.0,
    operating_range: tuple[float, float] = (-20.0, 20.0),
    machine_limit: tuple[float, float] = (-30.0, 30.0),
    cyclic_period: float | None = None,
) -> ShadowActuator:
    return ShadowActuator(
        id=actuator_id,
        label=actuator_id,
        coefficient=coefficient,
        unit="deg",
        max_step=max_step,
        max_session_delta=20.0,
        operating_range=operating_range,
        machine_limit=machine_limit,
        setpoint_key=f"{actuator_id}.set",
        readback_key=f"{actuator_id}.read",
        verify_tolerance=0.01,
        command_precision=3,
        cyclic_period=cyclic_period,
    )


def group(
    *actuators: ShadowActuator,
    response: float = 2.0,
    gain: float = 0.5,
    max_knob_step: float = 10.0,
) -> ShadowControlGroup:
    return ShadowControlGroup(
        id="group",
        label="Synthetic group",
        response_mev_per_knob_unit=response,
        loop_gain=gain,
        max_knob_step=max_knob_step,
        deadband_mev=0.01,
        action_window_mev=20.0,
        actuators=tuple(actuators),
    )


class DirectEnergyProviderTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient(
            {
                "energy": reading("energy", 100.0),
                "target": reading("target", 98.0),
                "diagnostic.spread": reading("diagnostic.spread", 0.2),
            }
        )
        self.provider = DirectEnergyProvider(
            self.client,
            valid_range_mev=(0.0, 2450.0),
            stale_timeout_s=3.0,
            diagnostic_keys=("diagnostic.spread",),
            median_window=3,
        )

    def test_direct_measurement_preserves_source_timestamp(self):
        result = self.provider.sample(now=101.0)
        self.assertTrue(result.quality.valid)
        self.assertEqual(result.source_timestamp, 100.0)
        self.assertEqual(result.target_source_timestamp, 100.0)
        self.assertEqual(result.energy_mev, 100.0)
        self.assertEqual(result.error_mev, 2.0)
        self.assertEqual(result.diagnostics["diagnostic.spread"], 0.2)
        self.assertEqual(
            result.diagnostic_source_timestamps["diagnostic.spread"],
            100.0,
        )

    def test_disconnect_stale_nan_and_range_enter_hold(self):
        cases = (
            reading("energy", 100.0, connected=False, error="offline"),
            reading("energy", 100.0, timestamp=90.0),
            reading("energy", math.nan),
            reading("energy", 2500.0),
        )
        for bad_reading in cases:
            with self.subTest(bad_reading=bad_reading):
                self.client.readings["energy"] = bad_reading
                result = self.provider.sample(now=101.0)
                self.assertEqual(result.quality.state, "HOLD")
                self.assertFalse(result.quality.valid)
        self.client.readings["energy"] = reading("energy", 100.0)
        self.client.readings["target"] = reading("target", 2500.0)
        self.assertEqual(self.provider.sample(now=101.0).quality.state, "HOLD")

    def test_filter_state_resets_after_invalid_sample(self):
        first = self.provider.sample(now=101.0)
        self.assertEqual(first.energy_mev, 100.0)
        self.client.readings["energy"] = reading("energy", 110.0, timestamp=101.0)
        second = self.provider.sample(now=102.0)
        self.assertEqual(second.energy_mev, 105.0)
        self.client.readings["energy"] = reading(
            "energy", None, timestamp=102.0, connected=False
        )
        self.assertEqual(self.provider.sample(now=102.0).quality.state, "HOLD")
        self.client.readings["energy"] = reading("energy", 130.0, timestamp=103.0)
        after_reset = self.provider.sample(now=103.0)
        self.assertEqual(after_reset.energy_mev, 130.0)

    def test_diagnostic_failure_is_recorded_but_not_control_input(self):
        self.client.readings["diagnostic.spread"] = reading(
            "diagnostic.spread", None, connected=False, error="offline"
        )
        result = self.provider.sample(now=101.0)
        self.assertTrue(result.quality.valid)
        self.assertIn("diagnostic.spread", result.diagnostic_errors)

    def test_local_target_mode_only_requires_fresh_energy(self):
        provider = DirectEnergyProvider(
            self.client,
            valid_range_mev=(0.0, 2450.0),
            stale_timeout_s=3.0,
            target_key=None,
        )
        self.client.readings["target"] = reading(
            "target", 98.0, timestamp=1.0, connected=False
        )
        result = provider.sample(now=101.0)
        self.assertTrue(result.quality.valid)
        self.assertEqual(result.energy_mev, 100.0)
        self.assertIsNone(result.target_mev)
        self.assertIsNone(result.error_mev)
        self.assertEqual(self.client.requests[-1], ("energy",))

    def test_static_external_target_is_not_rejected_as_stale(self):
        self.client.readings["target"] = reading("target", 98.0, timestamp=1.0)
        result = self.provider.sample(now=101.0)
        self.assertTrue(result.quality.valid)
        self.assertEqual(result.error_mev, 2.0)


class ShadowEngineTests(unittest.TestCase):
    def test_signed_response_controls_convergence_direction(self):
        readbacks = {"a": reading("a", 0.0)}
        positive = evaluate_shadow(
            measurement(4.0),
            group(actuator("a"), response=2.0),
            readbacks,
            stale_timeout_s=3.0,
            now=101.0,
        )
        negative = evaluate_shadow(
            measurement(4.0),
            group(actuator("a"), response=-2.0),
            readbacks,
            stale_timeout_s=3.0,
            now=101.0,
        )
        self.assertEqual(positive.state, "SHADOW")
        self.assertEqual(positive.knob_delta, -1.0)
        self.assertEqual(negative.knob_delta, 1.0)
        self.assertLess(positive.error_mev + 2.0 * positive.knob_delta, 4.0)
        self.assertLess(negative.error_mev - 2.0 * negative.knob_delta, 4.0)

    def test_n_actuator_vector_honours_each_step_limit(self):
        control_group = group(
            actuator("a", coefficient=1.0, max_step=5.0),
            actuator("b", coefficient=2.0, max_step=1.0),
            response=1.0,
            gain=1.0,
        )
        result = evaluate_shadow(
            measurement(10.0),
            control_group,
            {"a": reading("a", 0.0), "b": reading("b", 0.0)},
            stale_timeout_s=3.0,
            now=101.0,
        )
        self.assertEqual(result.knob_delta, -0.5)
        self.assertEqual([command.delta for command in result.commands], [-0.5, -1.0])

    def test_limit_and_invalid_input_produce_hold_without_commands(self):
        limited = group(
            actuator(
                "a",
                operating_range=(-1.0, 1.0),
                machine_limit=(-1.0, 1.0),
            ),
            response=1.0,
            gain=1.0,
        )
        result = evaluate_shadow(
            measurement(-1.0),
            limited,
            {"a": reading("a", 0.9)},
            stale_timeout_s=3.0,
            now=101.0,
        )
        self.assertEqual(result.state, "HOLD")
        self.assertEqual(result.commands, ())

        stale = evaluate_shadow(
            measurement(),
            group(actuator("a")),
            {"a": reading("a", 0.0, timestamp=90.0)},
            stale_timeout_s=3.0,
            now=101.0,
        )
        self.assertEqual(stale.state, "HOLD")
        self.assertEqual(stale.commands, ())

        invalid_response = evaluate_shadow(
            measurement(),
            group(actuator("a"), response=0.0),
            {"a": reading("a", 0.0)},
            stale_timeout_s=3.0,
            now=101.0,
        )
        self.assertEqual(invalid_response.state, "HOLD")
        self.assertEqual(invalid_response.commands, ())

    def test_cyclic_phase_crosses_positive_boundary_continuously(self):
        phase = actuator(
            "phase",
            max_step=1.0,
            operating_range=(-180.0, 180.0),
            machine_limit=(-180.0, 180.0),
            cyclic_period=360.0,
        )
        result = evaluate_shadow(
            measurement(0.6),
            group(phase, response=-1.0, gain=0.5, max_knob_step=1.0),
            {"phase": reading("phase", 179.9)},
            session_origins={"phase": 179.7},
            stale_timeout_s=3.0,
            now=101.0,
        )
        self.assertEqual(result.state, "SHADOW")
        self.assertAlmostEqual(result.commands[0].delta, 0.3)
        self.assertAlmostEqual(result.commands[0].proposed, -179.8)

    def test_unavailable_reason_is_stable(self):
        result = unavailable_decision()
        self.assertEqual(result.state, "HOLD")
        self.assertEqual(
            result.reason,
            "Feedback unavailable: actuator group not commissioned",
        )

    def test_operator_parameters_rebuild_ratio_and_independent_step_limits(self):
        configured = configure_control_group(
            group(actuator("a"), actuator("b", coefficient=-1.0)),
            loop_gain=0.1,
            ratio_actuator_id="b",
            ratio=1.25,
            ratio_sign=-1,
            max_knob_step=0.3,
            action_window_mev=5.0,
            max_session_delta=3.0,
        )
        self.assertEqual(configured.loop_gain, 0.1)
        self.assertEqual(configured.actuators[1].coefficient, -1.25)
        self.assertEqual(configured.actuators[0].max_step, 0.3)
        self.assertEqual(configured.actuators[1].max_step, 0.375)
        self.assertTrue(
            all(item.max_session_delta == 3.0 for item in configured.actuators)
        )


class FeedbackWriterTests(unittest.TestCase):
    def setUp(self):
        self.group = group(actuator("a"), actuator("b", coefficient=-1.0))
        self.readbacks = {
            "a": reading("a.read", 10.0),
            "b": reading("b.read", -20.0),
        }
        self.client = FakeWritableClient(
            {
                "a.read": self.readbacks["a"],
                "b.read": self.readbacks["b"],
            },
            {"a.set": "a.read", "b.set": "b.read"},
        )

    def _decision(self):
        return evaluate_shadow(
            measurement(2.0),
            self.group,
            self.readbacks,
            stale_timeout_s=3.0,
            now=101.0,
        )

    def test_pair_write_is_verified_from_independent_readbacks(self):
        result = apply_feedback_decision(
            self.client,
            self.group,
            self._decision(),
            timeout_s=0.1,
            poll_interval_s=0.01,
        )
        self.assertEqual(result.state, "APPLIED")
        self.assertTrue(result.write_verified)
        self.assertEqual([item[0] for item in self.client.writes], ["a.set", "b.set"])

    def test_second_write_failure_rolls_back_both_actuators(self):
        self.client.fail_once.add("b.set")
        result = apply_feedback_decision(
            self.client,
            self.group,
            self._decision(),
            timeout_s=0.1,
            poll_interval_s=0.01,
        )
        self.assertEqual(result.state, "HOLD")
        self.assertFalse(result.write_verified)
        self.assertTrue(result.rollback_applied)
        self.assertEqual(self.client.readings["a.read"].value, 10.0)
        self.assertEqual(self.client.readings["b.read"].value, -20.0)

    def test_manual_single_actuator_write_does_not_touch_the_other(self):
        result = apply_manual_targets(
            self.client,
            self.group,
            self.readbacks,
            {"a": 11.0},
            timeout_s=0.1,
            poll_interval_s=0.01,
        )
        self.assertTrue(result.write_verified)
        self.assertEqual(self.client.writes, [("a.set", 11.0)])

    def test_cyclic_manual_write_uses_shortest_phase_delta(self):
        phase = actuator(
            "phase",
            operating_range=(-180.0, 180.0),
            machine_limit=(-180.0, 180.0),
            cyclic_period=360.0,
        )
        control_group = group(phase)
        readbacks = {"phase": reading("phase.read", 179.9)}
        client = FakeWritableClient(
            {"phase.read": readbacks["phase"]},
            {"phase.set": "phase.read"},
        )
        result = apply_manual_targets(
            client,
            control_group,
            readbacks,
            {"phase": -179.9},
            timeout_s=0.1,
            poll_interval_s=0.01,
        )
        self.assertTrue(result.write_verified)
        self.assertAlmostEqual(result.commands[0].delta, 0.2)

    def test_epics_client_rejects_read_only_configured_keys(self):
        class FakePV:
            def __init__(self, _name, auto_monitor=True):
                self.connected = True

            def put(self, _value, **_kwargs):
                return 1

        with patch.dict(sys.modules, {"epics": SimpleNamespace(PV=FakePV)}):
            client = EpicsPVClient(
                {"energy": "SYN:ENERGY", "a.set": "SYN:A:SET"},
                writable_keys=("a.set",),
            )
        with self.assertRaises(PermissionError):
            client.write("energy", 1.0)
        self.assertTrue(client.write("a.set", 1.0))


def synthetic_profile() -> MachineProfile:
    energy = ElementConfig(
        id="SPECTROMETER_A",
        kind="diagnostic",
        display_name="Absolute energy",
        order=1,
        plane=None,
        roles=(),
        tags=(),
        limits={},
        channels={
            "absolute": {"real": "SYN:ENERGY"},
            "width": {"real": "SYN:WIDTH"},
        },
    )
    target = ElementConfig(
        id="REFERENCE_A",
        kind="energy",
        display_name="Energy reference",
        order=2,
        plane=None,
        roles=(),
        tags=(),
        limits={"demand": {"low": 10, "high": 500, "unit": "MeV"}},
        channels={"demand": {"real": "SYN:TARGET"}},
    )
    rf = ElementConfig(
        id="RF_A",
        kind="rf",
        display_name="RF A",
        order=3,
        plane=None,
        roles=(),
        tags=(),
        limits={"phase_set": {"low": -180, "high": 180, "unit": "deg"}},
        channels={
            "phase_set": {"real": "SYN:RF:SET"},
            "phase_readback": {"real": "SYN:RF:READ"},
        },
    )
    workflow = {
        "control_backends": ["real"],
        "real_status": "read_only",
        "write_control": {"default": "blocked", "real": "blocked"},
        "measurement": {
            "kind": "direct_energy",
            "label": "Synthetic Direct Energy",
            "capabilities": ["energy", "width"],
            "energy": {
                "element": "SPECTROMETER_A",
                "channel": "absolute",
                "unit": "MeV",
            },
            "diagnostics": {
                "width": {
                    "element": "SPECTROMETER_A",
                    "channel": "width",
                    "unit": "MeV",
                }
            },
            "valid_range_mev": [10, 500],
            "median_window": 1,
        },
        "target": {
            "element": "REFERENCE_A",
            "channel": "demand",
            "unit": "MeV",
        },
        "pulse": {"marker": None, "require_marker_for_write": True},
        "control_groups": [
            {
                "id": "rf_pair",
                "label": "RF pair",
                "response_mev_per_knob_unit": -1.5,
                "loop_gain": 0.2,
                "max_knob_step": 1.0,
                "deadband_mev": 0.1,
                "action_window_mev": 10.0,
                "actuators": [
                    {
                        "id": "rf_a",
                        "element": "RF_A",
                        "setpoint": "phase_set",
                        "readback": "phase_readback",
                        "coefficient": 1.0,
                        "unit": "deg",
                        "max_step": 1.0,
                        "max_session_delta": 5.0,
                        "verify_tolerance": 0.1,
                        "operating_range": [-170, 170],
                    }
                ],
            }
        ],
        "runtime": {
            "sample_period_s": 1,
            "plot_period_s": 1,
            "connection_timeout_s": 2,
            "stale_timeout_s": 3,
            "max_history_points": 100,
        },
        "logging": {"file_prefix": "generic_energy", "flush_every_n_rows": 1},
    }
    elements = (energy, target, rf)
    return MachineProfile(
        schema_version="1",
        machine=MachineConfig(
            id="synthetic",
            family="test",
            display_name="Synthetic Machine",
            default_mode="real",
            bpm_position_scale_to_mm={},
        ),
        control_backends=("real",),
        runtime=None,
        elements=elements,
        workflows={"energy_feedback": workflow},
        _elements_by_id={element.id: element for element in elements},
    )


class EnergyFeedbackProfileTests(unittest.TestCase):
    def test_half_loads_with_verified_llrf18_llrf19_write_mapping(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        workflow = resolve_energy_feedback_workflow(context.profile)
        config = load_runtime_config(context)
        self.assertEqual(
            real_commissioning_status(context), REAL_STATUS_WRITE_SMOKE_PASSED
        )
        self.assertTrue(config["write_enabled"])
        self.assertEqual(len(workflow["control_groups"]), 1)
        self.assertEqual(config["measurement_kind"], "energy_spectrum_target")
        self.assertEqual(config["measurement_label"], "Energy Spectrum Target")
        self.assertEqual(config["measurement_capabilities"], ("energy",))
        self.assertEqual(config["pvs"]["energy"], "IN:BD:ENY:H:E")
        self.assertNotIn("target", config["pvs"])
        self.assertIsNone(config["target_key"])
        self.assertEqual(config["diagnostic_keys"], ())
        self.assertEqual(config["signals"]["energy"]["unit"], "MeV")
        prf02 = context.profile.get_element("PRF02")
        self.assertEqual(
            prf02.channels["energy_actual"]["real"],
            "IN:BD:PRF02:H:E",
        )
        self.assertEqual(
            prf02.channels["energy_deviation"]["real"],
            "IN:BD:PRF02:H:DeltaE",
        )
        self.assertEqual(
            prf02.channels["energy_spread_sigma"]["real"],
            "IN:BD:PRF02:H:Sigma",
        )
        self.assertEqual(
            prf02.channels["energy_spread_fwhm"]["real"],
            "IN:BD:PRF02:H:DE",
        )
        group_config = config["control_groups"][0]
        self.assertEqual(
            [item.id for item in group_config.actuators],
            ["llrf18_phase", "llrf19_phase"],
        )
        self.assertTrue(
            all(item.cyclic_period == 360.0 for item in group_config.actuators)
        )
        self.assertEqual(
            config["pvs"][group_config.actuators[0].setpoint_key],
            "IN:MW:LLRF18:SET_PHASE",
        )
        self.assertEqual(
            config["pvs"][group_config.actuators[0].readback_key],
            "IN:MW:LLRF18:GET_PHASE",
        )
        self.assertEqual(
            config["pvs"][group_config.actuators[1].setpoint_key],
            "IN:MW:LLRF19:SET_PHASE",
        )
        self.assertEqual(
            config["pvs"][group_config.actuators[1].readback_key],
            "IN:MW:LLRF19:GET_PHASE",
        )
        with self.assertRaises(MachineProfileError):
            load_app_context(
                "energy_feedback", machine_id="half", control_backend="vm"
            )
        supported, reason = describe_app_support("irfel", "energy_feedback")
        self.assertFalse(supported)
        self.assertIn("energy_feedback.json", reason)

    def test_synthetic_second_machine_has_no_half_naming_dependency(self):
        profile = synthetic_profile()
        validate_energy_feedback_workflow(
            profile, profile.workflows["energy_feedback"], control_backend="real"
        )
        context = AppContext(
            app_name="energy_feedback",
            profile=profile,
            control_backend=ControlBackendConfig("real"),
        )
        config = load_runtime_config(context)
        self.assertEqual(config["machine_id"], "synthetic")
        self.assertEqual(config["pvs"]["energy"], "SYN:ENERGY")
        self.assertEqual(config["control_groups"][0].actuators[0].id, "rf_a")

    def test_invalid_element_unit_range_and_control_group_are_rejected(self):
        profile = synthetic_profile()
        base = profile.workflows["energy_feedback"]
        mutations = []

        unknown = deepcopy(base)
        unknown["measurement"]["energy"]["element"] = "MISSING"
        mutations.append(unknown)

        wrong_unit = deepcopy(base)
        wrong_unit["measurement"]["energy"]["unit"] = "GeV"
        mutations.append(wrong_unit)

        missing_diagnostic_unit = deepcopy(base)
        del missing_diagnostic_unit["measurement"]["diagnostics"]["width"]["unit"]
        mutations.append(missing_diagnostic_unit)

        mismatched_capabilities = deepcopy(base)
        mismatched_capabilities["measurement"]["capabilities"] = ["energy"]
        mutations.append(mismatched_capabilities)

        bad_range = deepcopy(base)
        bad_range["measurement"]["valid_range_mev"] = [0, 600]
        mutations.append(bad_range)

        same_channel = deepcopy(base)
        same_channel["control_groups"][0]["actuators"][0][
            "readback"
        ] = "phase_set"
        mutations.append(same_channel)

        zero_response = deepcopy(base)
        zero_response["control_groups"][0]["response_mev_per_knob_unit"] = 0
        mutations.append(zero_response)

        bad_period = deepcopy(base)
        bad_period["control_groups"][0]["actuators"][0]["cyclic_period"] = 360
        mutations.append(bad_period)

        for workflow in mutations:
            with self.subTest(workflow=workflow):
                with self.assertRaises(MachineProfileError):
                    validate_energy_feedback_workflow(profile, workflow)

    def test_write_capable_profile_requires_complete_safety_configuration(self):
        profile = synthetic_profile()
        workflow = deepcopy(profile.workflows["energy_feedback"])
        workflow["real_status"] = "write_smoke_passed"
        workflow["write_control"]["real"] = "allowed"
        workflow["pulse"]["require_marker_for_write"] = False
        with self.assertRaisesRegex(MachineProfileError, "write_verification"):
            validate_energy_feedback_workflow(profile, workflow)

        workflow["write_verification"] = {
            "timeout_s": 1.0,
            "poll_interval_s": 0.05,
            "command_precision": 2,
        }
        workflow["operator_controls"] = {
            "target": {"minimum": 10, "maximum": 500, "decimals": 3},
            "gain": {"minimum": 0.01, "maximum": 1.0, "decimals": 3},
            "actuator_ratio": {
                "actuator": "rf_a",
                "sign": 1,
                "minimum": 0.01,
                "maximum": 2.0,
                "decimals": 3,
            },
            "max_knob_step": {
                "minimum": 0.01,
                "maximum": 2.0,
                "decimals": 3,
            },
            "action_window": {
                "minimum": 0.1,
                "maximum": 20.0,
                "decimals": 3,
            },
            "max_session_delta": {
                "minimum": 0.1,
                "maximum": 10.0,
                "decimals": 3,
            },
        }
        validate_energy_feedback_workflow(profile, workflow)


class EnergyFeedbackLoggingTests(unittest.TestCase):
    def test_csv_is_flushed_and_metadata_contains_profile_snapshot(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        config = load_runtime_config(context)
        sample = measurement(2.0)
        decision = FeedbackDecision("HOLD", "monitor only", 2.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "half_linac.src.apps.energy_feedback.csv_logger.APP_DIR",
                Path(temp_dir),
            ):
                logger = EnergyFeedbackCsvLogger(context, config)
                logger.append(sample, decision)
                csv_path = logger.csv_path
                run_dir = logger.run_dir
                with csv_path.open(newline="", encoding="utf-8") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["decision_reason"], "monitor only")
                self.assertEqual(rows[0]["source_timestamp"], "100")
                logger.close()

                metadata = json.loads(
                    (run_dir / "metadata.json").read_text(encoding="utf-8")
                )
                self.assertEqual(metadata["mode"], "write_enabled")
                self.assertEqual(metadata["state"], "stopped")
                self.assertEqual(
                    metadata["measurement_provider"],
                    {
                        "kind": "energy_spectrum_target",
                        "label": "Energy Spectrum Target",
                        "capabilities": ["energy"],
                    },
                )
                self.assertEqual(
                    metadata["configuration"]["measurement"]["kind"],
                    "energy_spectrum_target",
                )


class EnergyFeedbackSafetyTests(unittest.TestCase):
    def test_application_tree_has_no_unscoped_caput_path(self):
        app_dir = REPO_ROOT / "src" / "apps" / "energy_feedback"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(app_dir.glob("*.py"))
        )
        self.assertNotIn("caput", source.lower())
        self.assertIn("client.write(", source)
        self.assertIn("require_workflow_write_allowed(", source)

    def test_entrypoint_uses_window_activation_api_with_window_only(self):
        source = (
            REPO_ROOT / "src" / "apps" / "energy_feedback" / "main.py"
        ).read_text(encoding="utf-8")
        self.assertIn("install_qt_window_raise_handler(window)", source)
        self.assertNotIn("install_qt_window_raise_handler(app, window)", source)

    def test_state_is_reset_between_sessions_and_feedback_needs_a_group(self):
        safety = FeedbackSafetyStateMachine(
            has_control_group=False, write_enabled=False
        )
        safety.start_acquisition()
        denied = safety.request_feedback(True)
        self.assertEqual(denied.state, "HOLD")
        self.assertFalse(denied.feedback_requested)
        stopped = safety.stop()
        self.assertEqual(stopped.state, "STOPPED")
        restarted = safety.start_acquisition()
        self.assertEqual(restarted.state, "CONNECTING")
        self.assertFalse(restarted.feedback_requested)

    def test_write_enabled_state_is_reset_between_sessions(self):
        safety = FeedbackSafetyStateMachine(
            has_control_group=True, write_enabled=True
        )
        safety.start_acquisition()
        self.assertTrue(safety.request_feedback(True).feedback_requested)
        self.assertFalse(safety.stop().feedback_requested)
        restarted = safety.start_acquisition()
        self.assertEqual(restarted.state, "CONNECTING")
        self.assertFalse(restarted.feedback_requested)


try:
    import os

    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        raise ImportError("offscreen Qt platform was not explicitly enabled")
    from PyQt5.QtWidgets import QApplication, QDoubleSpinBox, QGroupBox
    from half_linac.src.apps.energy_feedback.main import (
        DARK_THEME,
        LIGHT_THEME,
        MAX_RENDERED_PLOT_POINTS,
        EnergyFeedbackWindow,
        _decimated_indices,
    )
except (ImportError, RuntimeError):
    QApplication = None
    EnergyFeedbackWindow = None


@unittest.skipIf(
    QApplication is None or EnergyFeedbackWindow is None,
    "PyQt5 or Matplotlib Qt backend is not installed",
)
class EnergyFeedbackGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_half_write_controls_are_present_but_safe_until_connected(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        window = EnergyFeedbackWindow(context, auto_start=False)
        try:
            self.assertEqual(window.windowTitle(), "Energy Stabilizer")
            self.assertFalse(window.feedback_start_button.isEnabled())
            self.assertFalse(window.restore_initial_button.isEnabled())
            self.assertIn("WRITE ENABLED", window.write_value.text())
            self.assertAlmostEqual(window.gain_spin.value(), 0.1)
            self.assertAlmostEqual(window.ratio_spin.value(), 1.081423)
            self.assertEqual(window.connection_value.text(), "Not checked")
            self.assertEqual(
                tuple(window._status_items),
                ("data", "loop", "phase", "action", "access"),
            )
        finally:
            window.close()

    def test_numeric_fields_have_no_step_buttons_and_ignore_wheel(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        window = EnergyFeedbackWindow(context, auto_start=False)

        class WheelEventStub:
            def __init__(self):
                self.ignored = False

            def ignore(self):
                self.ignored = True

        try:
            spins = window.findChildren(QDoubleSpinBox)
            self.assertTrue(spins)
            for spin in spins:
                self.assertEqual(spin.buttonSymbols(), QDoubleSpinBox.NoButtons)
                before = spin.value()
                event = WheelEventStub()
                spin.wheelEvent(event)
                self.assertTrue(event.ignored)
                self.assertEqual(spin.value(), before)
        finally:
            window.close()

    def test_gui_retains_reference_shell_and_plot_controls(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        window = EnergyFeedbackWindow(context, auto_start=False)
        try:
            titles = {group.title() for group in window.findChildren(QGroupBox)}
            self.assertTrue(
                {
                    "Figure",
                    "Energy Tracking",
                    "Control",
                    "Phase Shifter",
                }.issubset(titles)
            )
            self.assertNotIn("Spectrum Diagnostics", titles)
            self.assertIsNone(window.energy_axis)
            self.assertIsNotNone(window.error_axis)
            self.assertIsNotNone(window.actuator_axis)
            self.assertEqual(len(window._plot_axes()), 2)
            self.assertEqual(
                [
                    window.plot_view_combo.itemText(index)
                    for index in range(window.plot_view_combo.count())
                ],
                ["Feedback", "Error Focus", "Full Detail"],
            )
            self.assertEqual(
                [
                    window.plot_range_combo.itemText(index)
                    for index in range(window.plot_range_combo.count())
                ],
                ["10 min", "30 min", "1 h", "All"],
            )
            self.assertEqual(window.plot_range_combo.currentText(), "30 min")
            self.assertEqual(
                [
                    window.plot_axis_combo.itemText(index)
                    for index in range(window.plot_axis_combo.count())
                ],
                ["Index", "Elapsed", "Clock"],
            )
            self.assertEqual(window.plot_toggle_button.text(), "Pause Plot")
            window.plot_toggle_button.click()
            self.assertFalse(window._plot_enabled)
            self.assertEqual(window.plot_toggle_button.text(), "Resume Plot")
            window.plot_toggle_button.click()
            self.assertTrue(window._plot_enabled)
            self.assertEqual(window.plot_toggle_button.text(), "Pause Plot")
            window.plot_view_combo.setCurrentText("Error Focus")
            self.assertIsNone(window.energy_axis)
            self.assertIsNotNone(window.error_axis)
            self.assertIsNone(window.actuator_axis)
            self.assertEqual(len(window._plot_axes()), 1)
            window.plot_view_combo.setCurrentText("Full Detail")
            self.assertIsNotNone(window.energy_axis)
            self.assertIsNotNone(window.error_axis)
            self.assertIsNotNone(window.actuator_axis)
            self.assertEqual(len(window._plot_axes()), 3)
        finally:
            window.close()

    def test_combo_popup_uses_readable_colors_in_both_themes(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        window = EnergyFeedbackWindow(context, auto_start=False)
        try:
            for theme in (DARK_THEME, LIGHT_THEME):
                if window._theme() is not theme:
                    window._toggle_theme()
                stylesheet = window.plot_view_combo.view().styleSheet()
                self.assertIn("QAbstractItemView::item:selected", stylesheet)
                self.assertIn(
                    f"background-color: {theme['section']}; color: {theme['text']};",
                    stylesheet,
                )
                self.assertIn(
                    f"background-color: {theme['metric']}; color: {theme['window']};",
                    stylesheet,
                )
                self.assertIn(
                    f"background-color: {theme['section']}; color: {theme['muted']};",
                    stylesheet,
                )
        finally:
            window.close()

    def test_provider_diagnostics_do_not_change_feedback_gui(self):
        profile = synthetic_profile()
        workflow = deepcopy(profile.workflows["energy_feedback"])
        workflow["measurement"]["label"] = "Dispersive BPM"
        profile = replace(profile, workflows={"energy_feedback": workflow})
        context = AppContext(
            app_name="energy_feedback",
            profile=profile,
            control_backend=ControlBackendConfig("real"),
        )
        window = EnergyFeedbackWindow(context, auto_start=False)
        try:
            titles = {group.title() for group in window.findChildren(QGroupBox)}
            self.assertNotIn("Spectrum Diagnostics", titles)
            self.assertEqual(len(window._plot_axes()), 2)
            self.assertEqual(window.plot_view_combo.count(), 3)
            self.assertEqual(
                [
                    window.plot_view_combo.itemText(index)
                    for index in range(window.plot_view_combo.count())
                ],
                ["Feedback", "Error Focus", "Full Detail"],
            )
            self.assertEqual(window.plot_view_combo.currentText(), "Feedback")
        finally:
            window.close()

    def test_plot_downsampling_is_bounded_and_preserves_extrema(self):
        values = [math.sin(index / 20.0) for index in range(10000)]
        values[1234] = -100.0
        values[8765] = 100.0
        indices = _decimated_indices(
            list(range(len(values))), values, MAX_RENDERED_PLOT_POINTS
        )
        self.assertLessEqual(len(indices), MAX_RENDERED_PLOT_POINTS)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], len(values) - 1)
        self.assertIn(1234, indices)
        self.assertIn(8765, indices)

    def test_feedback_views_render_expected_traces(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        window = EnergyFeedbackWindow(context, auto_start=False)
        try:
            history = {
                "time": [100.0, 101.0, 102.0],
                "energy": [100.0, 100.2, 100.1],
                "target": [100.0, 100.0, 100.0],
                "error": [0.0, 0.2, 0.1],
                "event.applied": [math.nan, 0.2, math.nan],
                "event.hold": [math.nan, math.nan, 0.1],
                "event.recovered": [math.nan, math.nan, math.nan],
                "actuator.llrf18_phase.readback_delta": [0.0, 0.1, 0.1],
                "actuator.llrf18_phase.command_delta": [math.nan, 0.1, math.nan],
                "actuator.llrf19_phase.readback_delta": [0.0, -0.1, -0.1],
                "actuator.llrf19_phase.command_delta": [math.nan, -0.1, math.nan],
            }
            for key, values in history.items():
                window._history[key].extend(values)
            window._draw_plot()
            self.assertIsNone(window.energy_axis)
            self.assertEqual(
                window.actuator_axis.get_ylabel(), "Phase Correction [deg]"
            )
            phase_labels = [
                line.get_label() for line in window.actuator_axis.lines
            ]
            self.assertIn("Phase 1 · LLRF18 Δ setpoint", phase_labels)
            self.assertFalse(any("command" in label for label in phase_labels))
            self.assertIn(
                "Measured − Target",
                [line.get_label() for line in window.error_axis.lines],
            )

            window.plot_view_combo.setCurrentText("Error Focus")
            self.assertEqual(len(window._plot_axes()), 1)
            self.assertIsNone(window.actuator_axis)

            window.plot_view_combo.setCurrentText("Full Detail")
            self.assertEqual(
                [line.get_label() for line in window.energy_axis.lines],
                ["Measured Energy", "Target Energy"],
            )
            self.assertEqual(
                window.actuator_axis.get_ylabel(), "Phase Correction [deg]"
            )

        finally:
            window.close()

    def test_phase_command_trace_excludes_shadow_only_proposals(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        window = EnergyFeedbackWindow(context, auto_start=False)
        readbacks = {
            "llrf18_phase": reading("llrf18_phase", 10.0),
            "llrf19_phase": reading("llrf19_phase", 20.0),
        }
        commands = (
            ActuatorCommand("llrf18_phase", 10.0, 1.0, 11.0, "deg"),
            ActuatorCommand("llrf19_phase", 20.0, -1.0, 19.0, "deg"),
        )
        try:
            window._on_sample(
                measurement(),
                FeedbackDecision("SHADOW", "proposal", 4.0, commands=commands),
                readbacks,
            )
            shadow_value = window._history[
                "actuator.llrf18_phase.command_delta"
            ][-1]
            self.assertTrue(math.isnan(shadow_value))

            window._on_sample(
                measurement(),
                FeedbackDecision(
                    "APPLIED",
                    "verified write",
                    4.0,
                    commands=commands,
                    write_verified=True,
                ),
                readbacks,
            )
            applied_value = window._history[
                "actuator.llrf18_phase.command_delta"
            ][-1]
            self.assertEqual(applied_value, 1.0)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
