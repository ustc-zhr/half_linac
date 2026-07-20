from __future__ import annotations

import copy
import json
import math
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.hv_feedback import epics_client
from half_linac.src.apps.hv_feedback.controller import (
    ControllerConfig,
    ControllerReference,
    IntegralHVController,
)
from half_linac.src.apps.hv_feedback.data_buffer import DataBuffer, Sample
from half_linac.src.apps.hv_feedback.epics_client import BaseClient, PVValue
from half_linac.src.apps.hv_feedback.profile_runtime import (
    amplitude_key,
    assert_hv_feedback_runtime,
    get_unit_config,
    load_profile_config,
    load_runtime_snapshot,
    phase_key,
    require_confirmed_feedback_write,
    required_signal_keys,
    snapshot_payload,
    validate_session_config,
)
from half_linac.src.apps.hv_feedback.reference import (
    auto_reference,
    validate_reference_values,
)
from half_linac.src.apps.hv_feedback.runtime import FeedbackEngine
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    REAL_STATUS_COMMISSIONED,
    describe_app_support,
    get_workflow,
    load_app_context,
    real_commissioning_status,
    workflow_writes_allowed,
)
from half_linac.src.shared.machine_profile.loader import _validate_hv_feedback_workflow


class FakeClient(BaseClient):
    def __init__(self, values: dict[str, float], invalid: set[str] | None = None):
        self.values = dict(values)
        self.invalid = set(invalid or ())
        self.writes: list[tuple[str, float]] = []

    def read_many(self, keys):
        now = time.time()
        return {
            key: PVValue(
                key=key,
                name=f"TEST:{key}",
                value=None if key in self.invalid else float(self.values[key]),
                timestamp=now,
                ok=key not in self.invalid,
                error="invalid test PV" if key in self.invalid else "",
            )
            for key in keys
        }

    def put(self, key: str, value: float) -> None:
        self.writes.append((key, float(value)))
        self.values[key] = float(value)


def unit_config(context, unit_id: str = "kly1") -> dict:
    return get_unit_config(load_profile_config(context), unit_id)


def reference_values(config: dict) -> dict[str, float]:
    values = {
        "hv_setpoint": float(config["reference"]["hv_kv"]),
        "hv_readback": float(config["reference"]["hv_kv"]),
    }
    for channel in config["rf_channels"]:
        channel_id = str(channel["id"])
        ref = config["reference"]["channels"][channel_id]
        values[amplitude_key(channel_id)] = float(ref["amplitude"])
        values[phase_key(channel_id)] = float(ref["phase_deg"])
    return values


class HVFeedbackProfileTests(unittest.TestCase):
    def setUp(self):
        self.real_context = load_app_context(
            "hv_feedback", machine_id="irfel", control_backend="real"
        )

    def test_profile_resolves_commissioned_multi_unit_topology(self):
        profile_config = load_profile_config(self.real_context)
        self.assertEqual(profile_config["unit_order"], ["kly1"])
        config = get_unit_config(profile_config, "kly1")
        self.assertEqual(config["default_feedback_channel"], "acc1")
        self.assertEqual(
            [channel["id"] for channel in config["rf_channels"]],
            ["acc1", "buncher"],
        )
        self.assertEqual(
            {key: config["pvs"][key]["name"] for key in required_signal_keys(config)},
            {
                "hv_setpoint": "IRFEL:modulator1:HV_set:ao",
                "hv_readback": "IRFEL:modulator1:HV:ai",
                "rf.acc1.amplitude": "IRFEL:IN-MW:KLY1:GET_CH3_AMP",
                "rf.acc1.phase": "IRFEL:IN-MW:KLY1:GET_CH3_PHASE",
                "rf.buncher.amplitude": "IRFEL:IN-MW:KLY1:GET_CH4_AMP",
                "rf.buncher.phase": "IRFEL:IN-MW:KLY1:GET_CH4_PHASE",
            },
        )
        self.assertEqual(
            real_commissioning_status(self.real_context, "hv_feedback"),
            REAL_STATUS_COMMISSIONED,
        )
        self.assertTrue(workflow_writes_allowed(self.real_context, "hv_feedback"))

    def test_profile_rejects_duplicate_unit_channel_and_write_target(self):
        workflow = copy.deepcopy(get_workflow(self.real_context.profile, "hv_feedback"))
        duplicate_unit = copy.deepcopy(workflow["feedback_units"][0])
        workflow["feedback_units"].append(duplicate_unit)
        with self.assertRaisesRegex(MachineProfileError, "duplicates"):
            _validate_hv_feedback_workflow(self.real_context.profile, workflow)

        workflow = copy.deepcopy(get_workflow(self.real_context.profile, "hv_feedback"))
        duplicate_unit = copy.deepcopy(workflow["feedback_units"][0])
        duplicate_unit["id"] = "second"
        duplicate_unit["label"] = "Second unit"
        workflow["feedback_units"].append(duplicate_unit)
        with self.assertRaisesRegex(MachineProfileError, "already used"):
            _validate_hv_feedback_workflow(self.real_context.profile, workflow)

        workflow = copy.deepcopy(get_workflow(self.real_context.profile, "hv_feedback"))
        workflow["feedback_units"][0]["rf_channels"].append(
            copy.deepcopy(workflow["feedback_units"][0]["rf_channels"][0])
        )
        with self.assertRaisesRegex(MachineProfileError, "duplicates"):
            _validate_hv_feedback_workflow(self.real_context.profile, workflow)

    def test_write_confirmation_revalidates_unit_and_target(self):
        config = unit_config(self.real_context)
        target = config["pvs"]["hv_setpoint"]["name"]
        require_confirmed_feedback_write(
            self.real_context,
            session_confirmed=True,
            feedback_unit_id="kly1",
            target_pv=target,
        )
        with self.assertRaisesRegex(MachineProfileError, "not been explicitly confirmed"):
            require_confirmed_feedback_write(
                self.real_context,
                session_confirmed=False,
                feedback_unit_id="kly1",
                target_pv=target,
            )
        with self.assertRaisesRegex(MachineProfileError, "write target changed"):
            require_confirmed_feedback_write(
                self.real_context,
                session_confirmed=True,
                feedback_unit_id="kly1",
                target_pv="WRONG:PV",
            )

    def test_product_epics_adapter_has_no_mock_client(self):
        self.assertFalse(hasattr(epics_client, "MockClient"))

    def test_half_and_irfel_vm_are_not_operational_targets(self):
        supported, reason = describe_app_support("half", "hv_feedback")
        self.assertFalse(supported)
        self.assertIn("hv_feedback.json", reason or "")
        vm_context = load_app_context(
            "hv_feedback", machine_id="irfel", control_backend="vm"
        )
        self.assertEqual(
            get_workflow(vm_context.profile, "hv_feedback")["control_backends"],
            ["real"],
        )
        self.assertFalse(workflow_writes_allowed(vm_context, "hv_feedback"))
        with self.assertRaisesRegex(MachineProfileError, "IRFEL real-machine"):
            assert_hv_feedback_runtime(vm_context)

    def test_schema_2_snapshot_is_unit_scoped_and_has_no_topology_overrides(self):
        config = unit_config(self.real_context)
        payload = snapshot_payload(config)
        self.assertEqual(
            set(payload),
            {"schema_version", "feedback_unit_id", "control", "reference", "safety"},
        )
        self.assertNotIn("pvs", payload)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit_dir = root / "kly1"
            unit_dir.mkdir()
            bad_path = unit_dir / "bad.json"
            bad_payload = dict(payload, pvs={"hv_setpoint": "WRONG:PV"})
            bad_path.write_text(json.dumps(bad_payload), encoding="utf-8")
            with patch(
                "half_linac.src.apps.hv_feedback.profile_runtime.resolve_hv_feedback_runtime_paths",
                return_value={"snapshots_dir": unit_dir, "snapshots_root": root},
            ):
                with self.assertRaisesRegex(ValueError, "unsupported top-level"):
                    load_runtime_snapshot(self.real_context, bad_path, config)

            wrong_path = unit_dir / "wrong-unit.json"
            wrong_path.write_text(
                json.dumps(dict(payload, feedback_unit_id="other")), encoding="utf-8"
            )
            with patch(
                "half_linac.src.apps.hv_feedback.profile_runtime.resolve_hv_feedback_runtime_paths",
                return_value={"snapshots_dir": unit_dir, "snapshots_root": root},
            ):
                with self.assertRaisesRegex(ValueError, "belongs to feedback unit"):
                    load_runtime_snapshot(self.real_context, wrong_path, config)

            root_path = root / "schema-2-in-root.json"
            root_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch(
                "half_linac.src.apps.hv_feedback.profile_runtime.resolve_hv_feedback_runtime_paths",
                return_value={"snapshots_dir": unit_dir, "snapshots_root": root},
            ):
                with self.assertRaisesRegex(ValueError, "this unit's runtime directory"):
                    load_runtime_snapshot(self.real_context, root_path, config)

    def test_schema_1_irfel_snapshot_is_migrated(self):
        config = unit_config(self.real_context)
        legacy = {
            "schema_version": 1,
            "control": copy.deepcopy(config["control"]),
            "reference": {
                "acc1_amp_ref": 100.0,
                "acc1_phase_ref": 10.0,
                "buncher_phase_ref": 20.0,
                "amp_ratio_ref": 1.4,
                "hv0": 39.0,
            },
            "safety": {
                "hv_min_kv": 38.5,
                "hv_max_kv": 39.5,
                "hv_readback_tolerance_kv": 0.02,
                "acc1_phase_limit_deg": 1.0,
                "buncher_phase_limit_deg": 1.0,
                "amp_ratio_limit_rel": 0.02,
                "acc1_amp_min_rel": 0.95,
                "acc1_amp_max_rel": 1.05,
                "require_valid_pv": True,
                "hold_on_fault": True,
            },
        }
        legacy["control"].pop("reference_samples")
        legacy["control"].pop("reference_sample_interval_s")
        legacy["control"]["init_window_s"] = 30.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "legacy.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            with patch(
                "half_linac.src.apps.hv_feedback.profile_runtime.resolve_hv_feedback_runtime_paths",
                return_value={"snapshots_dir": root / "kly1", "snapshots_root": root},
            ):
                migrated = load_runtime_snapshot(self.real_context, path, config)
        self.assertEqual(migrated["control"]["reference_samples"], 30)
        self.assertEqual(
            migrated["reference"]["channels"]["buncher"]["amplitude"], 140.0
        )


class HVFeedbackCoreTests(unittest.TestCase):
    def setUp(self):
        context = load_app_context(
            "hv_feedback", machine_id="irfel", control_backend="real"
        )
        self.config = unit_config(context)
        self.values = reference_values(self.config)

    def test_integral_controller_direction_limits_and_selected_signal(self):
        controller = IntegralHVController(
            ControllerConfig(2.0, 0.02, 0.2),
            ControllerReference(feedback_amplitude_ref=100.0, hv0=39.0),
            "rf.test.amplitude",
        )
        result = controller.compute({"rf.test.amplitude": 90.0}, 39.0)
        self.assertGreater(result.delta_hv, 0)
        self.assertAlmostEqual(result.delta_hv, 0.02)
        self.assertTrue(result.saturated_step)
        total_limited = controller.compute({"rf.test.amplitude": 90.0}, 39.19)
        self.assertAlmostEqual(total_limited.hv_next, 39.2)
        self.assertTrue(total_limited.saturated_total)

    def test_buffer_uses_circular_phase_mean_for_dynamic_keys(self):
        buffer = DataBuffer(max_age_s=10.0)
        now = time.time()
        buffer.append(Sample(now, {"rf.acc1.phase": 179.0}, True, {}))
        buffer.append(Sample(now, {"rf.acc1.phase": -179.0}, True, {}))
        aggregate = buffer.aggregate(10.0)
        assert aggregate is not None
        self.assertTrue(
            math.isclose(abs(aggregate["rf.acc1.phase"]), 180.0, abs_tol=1e-9)
        )

    def test_one_to_one_has_no_ratio_requirement(self):
        config = copy.deepcopy(self.config)
        config["rf_channels"] = config["rf_channels"][:1]
        config["reference"]["channels"] = {
            "acc1": config["reference"]["channels"]["acc1"]
        }
        config["safety"]["phase_limit_deg"] = {
            "acc1": config["safety"]["phase_limit_deg"]["acc1"]
        }
        config["pvs"] = {
            key: value
            for key, value in config["pvs"].items()
            if key in {"hv_setpoint", "hv_readback", amplitude_key("acc1"), phase_key("acc1")}
        }
        validate_session_config(config)
        client = FakeClient(reference_values(config))
        rows = FeedbackEngine(
            config,
            mode="monitor",
            feedback_channel_id="acc1",
            client=client,
        ).step()
        self.assertIn("MONITOR", {row["event"] for row in rows})
        self.assertFalse(client.writes)

    def test_one_to_three_checks_each_monitored_ratio(self):
        config = copy.deepcopy(self.config)
        config["rf_channels"].append({"id": "acc2", "label": "ACC2"})
        config["reference"]["channels"]["acc2"] = {
            "amplitude": 200.0,
            "phase_deg": 30.0,
        }
        config["safety"]["phase_limit_deg"]["acc2"] = 1.0
        config["pvs"][amplitude_key("acc2")] = {
            "name": "TEST:ACC2:AMP", "unit": "a.u.", "element": "ACC2", "channel": "amp"
        }
        config["pvs"][phase_key("acc2")] = {
            "name": "TEST:ACC2:PHASE", "unit": "deg", "element": "ACC2", "channel": "phase"
        }
        validate_session_config(config)
        values = reference_values(config)
        values[amplitude_key("acc2")] *= 1.1
        engine = FeedbackEngine(
            config,
            mode="feedback",
            feedback_channel_id="acc1",
            client=FakeClient(values),
            write_authorizer=lambda: None,
        )
        rows = engine.step()
        self.assertEqual(engine.state, "HOLD")
        self.assertTrue(any("acc2/acc1" in str(row.get("reason")) for row in rows))

    def test_switching_feedback_channel_changes_control_error_source(self):
        values = dict(self.values)
        values[amplitude_key("acc1")] *= 0.995
        values[amplitude_key("buncher")] *= 1.005
        acc1_rows = FeedbackEngine(
            self.config,
            mode="monitor",
            feedback_channel_id="acc1",
            client=FakeClient(values),
        ).step()
        buncher_rows = FeedbackEngine(
            self.config,
            mode="monitor",
            feedback_channel_id="buncher",
            client=FakeClient(values),
        ).step()
        acc1_update = next(row for row in acc1_rows if row["event"] == "MONITOR")
        buncher_update = next(row for row in buncher_rows if row["event"] == "MONITOR")
        self.assertGreater(acc1_update["error_rel"], 0)
        self.assertLess(buncher_update["error_rel"], 0)

    def test_reference_validation_and_measurement_are_channel_dynamic(self):
        bad = copy.deepcopy(self.config["reference"])
        bad["channels"]["acc1"]["amplitude"] = 0.0
        self.assertIn("too small", validate_reference_values(bad, self.config) or "")
        buffer = DataBuffer(max_age_s=None)
        now = time.time()
        for index, scale in enumerate((0.9, 1.1)):
            values = reference_values(self.config)
            values[amplitude_key("acc1")] *= scale
            values[amplitude_key("buncher")] *= scale
            values[phase_key("acc1")] = 179.0 if index == 0 else -179.0
            buffer.append(Sample(now + index, values, True, {}))
        result = auto_reference(buffer, self.config)
        assert result.reference is not None
        self.assertAlmostEqual(
            result.reference.channel_amplitudes["acc1"],
            self.config["reference"]["channels"]["acc1"]["amplitude"],
        )
        self.assertTrue(
            math.isclose(
                abs(result.reference.channel_phases["acc1"]),
                180.0,
                abs_tol=1e-9,
            )
        )

    def test_monitor_never_writes_feedback_requires_authorizer(self):
        client = FakeClient(self.values)
        engine = FeedbackEngine(
            self.config,
            mode="monitor",
            feedback_channel_id="acc1",
            client=client,
        )
        self.assertIn("MONITOR", {row["event"] for row in engine.step()})
        self.assertFalse(client.writes)
        with self.assertRaisesRegex(ValueError, "write authorizer"):
            FeedbackEngine(
                self.config,
                mode="feedback",
                feedback_channel_id="acc1",
                client=FakeClient(self.values),
            )

    def test_confirmed_feedback_writes_and_stop_blocks_future_writes(self):
        client = FakeClient(self.values)
        checks: list[str] = []
        engine = FeedbackEngine(
            self.config,
            mode="feedback",
            feedback_channel_id="acc1",
            client=client,
            write_authorizer=lambda: checks.append("checked"),
        )
        self.assertIn("CAPUT_HV", {row["event"] for row in engine.step()})
        self.assertEqual(len(client.writes), 1)
        engine.stop_row()
        self.assertEqual(engine.step(), [])
        self.assertEqual(checks, ["checked"])

    def test_invalid_pv_phase_fault_and_authorization_failure_hold(self):
        invalid_client = FakeClient(self.values, {phase_key("acc1")})
        invalid_engine = FeedbackEngine(
            self.config,
            mode="feedback",
            feedback_channel_id="acc1",
            client=invalid_client,
            write_authorizer=lambda: None,
        )
        invalid_engine.step()
        self.assertEqual(invalid_engine.state, "HOLD")
        self.assertFalse(invalid_client.writes)

        unsafe = dict(self.values)
        unsafe[phase_key("acc1")] += 5.0
        unsafe_engine = FeedbackEngine(
            self.config,
            mode="feedback",
            feedback_channel_id="acc1",
            client=FakeClient(unsafe),
            write_authorizer=lambda: None,
        )
        unsafe_engine.step()
        self.assertEqual(unsafe_engine.state, "HOLD")

        denied = FeedbackEngine(
            self.config,
            mode="feedback",
            feedback_channel_id="acc1",
            client=FakeClient(self.values),
            write_authorizer=lambda: (_ for _ in ()).throw(MachineProfileError("blocked")),
        )
        denied.step()
        self.assertEqual(denied.state, "HOLD")


if __name__ == "__main__":
    unittest.main()
