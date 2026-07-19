from __future__ import annotations

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

from half_linac.src.apps.hv_feedback.controller import (
    ControllerConfig,
    ControllerReference,
    IntegralHVController,
)
from half_linac.src.apps.hv_feedback.data_buffer import DataBuffer, Sample
from half_linac.src.apps.hv_feedback.epics_client import BaseClient, PVValue
from half_linac.src.apps.hv_feedback import epics_client
from half_linac.src.apps.hv_feedback.profile_runtime import (
    assert_hv_feedback_runtime,
    load_profile_config,
    load_runtime_snapshot,
    require_confirmed_feedback_write,
    snapshot_payload,
    validate_session_config,
)
from half_linac.src.apps.hv_feedback.reference import (
    auto_reference,
    validate_reference_values,
)
from half_linac.src.apps.hv_feedback.runtime import FeedbackEngine, REQUIRED_KEYS
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    REAL_STATUS_COMMISSIONED,
    describe_app_support,
    get_workflow,
    load_app_context,
    real_commissioning_status,
    workflow_writes_allowed,
)


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


class HVFeedbackProfileTests(unittest.TestCase):
    def setUp(self):
        self.real_context = load_app_context(
            "hv_feedback",
            machine_id="irfel",
            control_backend="real",
        )

    def test_profile_resolves_commissioned_real_pvs(self):
        config = load_profile_config(self.real_context)
        self.assertEqual(
            {key: config["pvs"][key]["name"] for key in REQUIRED_KEYS},
            {
                "hv_setpoint": "IRFEL:modulator1:HV_set:ao",
                "hv_readback": "IRFEL:modulator1:HV:ai",
                "acc1_amp": "IRFEL:IN-MW:KLY1:GET_CH3_AMP",
                "acc1_phase": "IRFEL:IN-MW:KLY1:GET_CH3_PHASE",
                "buncher_amp": "IRFEL:IN-MW:KLY1:GET_CH4_AMP",
                "buncher_phase": "IRFEL:IN-MW:KLY1:GET_CH4_PHASE",
            },
        )
        self.assertEqual(
            real_commissioning_status(self.real_context, "hv_feedback"),
            REAL_STATUS_COMMISSIONED,
        )
        self.assertTrue(workflow_writes_allowed(self.real_context, "hv_feedback"))
        self.assertEqual(config["control"]["reference_samples"], 30)
        self.assertEqual(config["control"]["reference_sample_interval_s"], 1.0)
        with self.assertRaisesRegex(MachineProfileError, "not been explicitly confirmed"):
            require_confirmed_feedback_write(
                self.real_context,
                session_confirmed=False,
            )

    def test_product_epics_adapter_has_no_mock_client(self):
        self.assertFalse(hasattr(epics_client, "MockClient"))

    def test_half_and_irfel_vm_are_not_operational_targets(self):
        supported, reason = describe_app_support("half", "hv_feedback")
        self.assertFalse(supported)
        self.assertIn("hv_feedback.json", reason or "")

        vm_context = load_app_context(
            "hv_feedback",
            machine_id="irfel",
            control_backend="vm",
        )
        self.assertEqual(
            get_workflow(vm_context.profile, "hv_feedback")["control_backends"],
            ["real"],
        )
        self.assertFalse(workflow_writes_allowed(vm_context, "hv_feedback"))
        with self.assertRaisesRegex(MachineProfileError, "IRFEL real-machine"):
            assert_hv_feedback_runtime(vm_context)

    def test_runtime_snapshots_never_include_pv_or_backend_overrides(self):
        config = load_profile_config(self.real_context)
        payload = snapshot_payload(config)
        self.assertEqual(
            set(payload),
            {"schema_version", "control", "reference", "safety"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            snapshots_dir = Path(tmp)
            snapshot_path = snapshots_dir / "bad.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "control": payload["control"],
                        "reference": payload["reference"],
                        "safety": payload["safety"],
                        "pvs": {"hv_setpoint": "WRONG:PV"},
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "half_linac.src.apps.hv_feedback.profile_runtime.resolve_hv_feedback_runtime_paths",
                return_value={"snapshots_dir": snapshots_dir},
            ):
                with self.assertRaisesRegex(ValueError, "unsupported top-level"):
                    load_runtime_snapshot(self.real_context, snapshot_path, config)

    def test_legacy_reference_window_snapshot_is_migrated(self):
        config = load_profile_config(self.real_context)
        payload = snapshot_payload(config)
        payload["control"].pop("reference_samples")
        payload["control"].pop("reference_sample_interval_s")
        payload["control"]["init_window_s"] = 30.0

        with tempfile.TemporaryDirectory() as tmp:
            snapshots_dir = Path(tmp)
            snapshot_path = snapshots_dir / "legacy.json"
            snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch(
                "half_linac.src.apps.hv_feedback.profile_runtime.resolve_hv_feedback_runtime_paths",
                return_value={"snapshots_dir": snapshots_dir},
            ):
                migrated = load_runtime_snapshot(self.real_context, snapshot_path, config)

        self.assertNotIn("init_window_s", migrated["control"])
        self.assertEqual(migrated["control"]["reference_samples"], 30)
        self.assertEqual(migrated["control"]["reference_sample_interval_s"], 1.0)


class HVFeedbackCoreTests(unittest.TestCase):
    def setUp(self):
        context = load_app_context(
            "hv_feedback",
            machine_id="irfel",
            control_backend="real",
        )
        self.config = load_profile_config(context)
        reference = self.config["reference"]
        self.values = {
            "hv_setpoint": float(reference["hv0"]),
            "hv_readback": float(reference["hv0"]),
            "acc1_amp": float(reference["acc1_amp_ref"]),
            "acc1_phase": float(reference["acc1_phase_ref"]),
            "buncher_amp": float(reference["acc1_amp_ref"])
            * float(reference["amp_ratio_ref"]),
            "buncher_phase": float(reference["buncher_phase_ref"]),
        }

    def test_integral_controller_direction_and_limits(self):
        controller = IntegralHVController(
            ControllerConfig(
                gain_kv_per_relerr=2.0,
                max_step_kv=0.02,
                total_limit_kv=0.2,
            ),
            ControllerReference(acc1_amp_ref=100.0, hv0=39.0),
        )
        result = controller.compute({"acc1_amp": 90.0}, hv_setpoint_now=39.0)
        self.assertGreater(result.delta_hv, 0)
        self.assertAlmostEqual(result.delta_hv, 0.02)
        self.assertTrue(result.saturated_step)

        total_limited = controller.compute({"acc1_amp": 90.0}, hv_setpoint_now=39.19)
        self.assertAlmostEqual(total_limited.hv_next, 39.2)
        self.assertTrue(total_limited.saturated_total)

    def test_buffer_uses_circular_phase_mean(self):
        buffer = DataBuffer(max_age_s=10.0)
        now = time.time()
        buffer.append(Sample(now, {"acc1_phase": 179.0}, True, {}))
        buffer.append(Sample(now, {"acc1_phase": -179.0}, True, {}))
        aggregate = buffer.aggregate(10.0)
        self.assertIsNotNone(aggregate)
        assert aggregate is not None
        self.assertTrue(math.isclose(abs(aggregate["acc1_phase"]), 180.0, abs_tol=1e-9))

    def test_reference_validation_rejects_invalid_values(self):
        values = dict(self.config["reference"])
        values["acc1_amp_ref"] = 0.0
        reason = validate_reference_values(values, self.config["safety"])
        self.assertIn("too small", reason or "")

    def test_reference_sample_count_must_be_an_integer(self):
        config = json.loads(json.dumps(self.config))
        config["control"]["reference_samples"] = 3.5
        with self.assertRaisesRegex(ValueError, "Reference samples"):
            validate_session_config(config)

    def test_auto_reference_uses_all_collected_samples(self):
        buffer = DataBuffer(max_age_s=None)
        now = time.time()
        for index, amplitude in enumerate((90.0, 100.0, 110.0)):
            buffer.append(
                Sample(
                    now + index,
                    {
                        "acc1_amp": amplitude,
                        "acc1_phase": 179.0 if index != 1 else -179.0,
                        "buncher_amp": amplitude * 1.4,
                        "buncher_phase": -20.0,
                        "hv_readback": 39.0,
                    },
                    True,
                    {},
                )
            )
        result = auto_reference(buffer, self.config["safety"])
        self.assertIsNotNone(result.reference)
        assert result.reference is not None
        self.assertAlmostEqual(result.reference.acc1_amp_ref, 100.0)
        self.assertAlmostEqual(result.reference.amp_ratio_ref, 1.4)

    def test_monitor_never_writes(self):
        client = FakeClient(self.values)
        engine = FeedbackEngine(self.config, mode="monitor", client=client)
        rows = engine.step()
        self.assertFalse(client.writes)
        self.assertIn("MONITOR", {row["event"] for row in rows})

    def test_feedback_requires_an_authorizer(self):
        with self.assertRaisesRegex(ValueError, "write authorizer"):
            FeedbackEngine(
                self.config,
                mode="feedback",
                client=FakeClient(self.values),
            )

    def test_confirmed_feedback_writes_and_stop_prevents_more_writes(self):
        client = FakeClient(self.values)
        authorizations: list[str] = []
        engine = FeedbackEngine(
            self.config,
            mode="feedback",
            client=client,
            write_authorizer=lambda: authorizations.append("checked"),
        )
        rows = engine.step()
        self.assertEqual(len(client.writes), 1)
        self.assertEqual(authorizations, ["checked"])
        self.assertIn("CAPUT_HV", {row["event"] for row in rows})

        engine.last_update_time = 0.0
        engine.step()
        self.assertEqual(len(client.writes), 2)
        self.assertEqual(authorizations, ["checked", "checked"])

        engine.stop_row()
        self.assertEqual(engine.step(), [])
        self.assertEqual(len(client.writes), 2)

    def test_failed_authorization_enters_hold_and_stays_write_blocked(self):
        client = FakeClient(self.values)

        def deny_write() -> None:
            raise MachineProfileError("blocked by test policy")

        engine = FeedbackEngine(
            self.config,
            mode="feedback",
            client=client,
            write_authorizer=deny_write,
        )
        rows = engine.step()
        self.assertFalse(client.writes)
        self.assertEqual(engine.state, "HOLD")
        self.assertIn("HOLD", {row["event"] for row in rows})
        engine.step()
        self.assertFalse(client.writes)

    def test_invalid_pv_or_safety_fault_enters_hold_without_writing(self):
        invalid_client = FakeClient(self.values, invalid={"acc1_phase"})
        invalid_engine = FeedbackEngine(
            self.config,
            mode="feedback",
            client=invalid_client,
            write_authorizer=lambda: None,
        )
        invalid_engine.step()
        self.assertEqual(invalid_engine.state, "HOLD")
        self.assertFalse(invalid_client.writes)

        unsafe_values = dict(self.values)
        unsafe_values["acc1_phase"] += 5.0
        unsafe_client = FakeClient(unsafe_values)
        unsafe_engine = FeedbackEngine(
            self.config,
            mode="feedback",
            client=unsafe_client,
            write_authorizer=lambda: None,
        )
        unsafe_engine.step()
        self.assertEqual(unsafe_engine.state, "HOLD")
        self.assertFalse(unsafe_client.writes)


if __name__ == "__main__":
    unittest.main()
