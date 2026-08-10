from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PARENT = REPO_ROOT.parent
if str(REPO_PARENT) not in sys.path:
    sys.path.insert(0, str(REPO_PARENT))

from half_linac.src.apps.ct_monitor.model import (
    MonitorStore,
    ShotPairer,
    SignalSample,
    TransmissionSample,
    calculate_efficiency,
    downsample_scalar_points,
    downsample_transmission_samples,
    parse_bounded_integer_input,
    rolling_statistics,
)
from half_linac.src.shared.elegant_backend.parser import _load_watch_scalar_from_sdds
from half_linac.src.shared.elegant_backend.publisher import (
    VmPublishPlan,
    VmPublisher,
    VmWatchScalarPublishSpec,
    build_vm_publish_plan,
    reconcile_watch_scalar_sources,
)
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    describe_app_support,
    get_workflow,
    list_elements,
    load_app_context,
    load_profile,
    resolve_channel,
    resolve_ct_monitor_workflow,
)
from half_linac.src.shared.machine_profile.loader import _normalize_ct_monitor_workflow
from half_linac.src.virtual_machine.lattice_parser import lattice_parser


class CTMonitorCalculationTests(unittest.TestCase):
    def test_efficiency_uses_absolute_measurements_without_clamping(self):
        self.assertEqual(calculate_efficiency(-2.0, 1.0, 0.01), 50.0)
        self.assertEqual(calculate_efficiency(1.0, -1.2, 0.01), 120.0)

    def test_efficiency_rejects_noise_and_nonfinite_values(self):
        self.assertIsNone(calculate_efficiency(0.009, 1.0, 0.01))
        self.assertIsNone(calculate_efficiency(math.nan, 1.0, 0.01))

    def test_rolling_statistics_uses_last_window(self):
        samples = [
            TransmissionSample(float(index), 1.0, 1.0, value)
            for index, value in enumerate((10.0, 20.0, 30.0))
        ]
        mean, stddev = rolling_statistics(samples, 2)
        self.assertEqual(mean, 25.0)
        self.assertEqual(stddev, 5.0)

    def test_manual_window_input_requires_an_integer_inside_the_configured_range(self):
        self.assertEqual(parse_bounded_integer_input(" 90 ", 10, 1800), 90)
        self.assertEqual(parse_bounded_integer_input("5", 5, 1000), 5)
        self.assertIsNone(parse_bounded_integer_input("9", 10, 1800))
        self.assertIsNone(parse_bounded_integer_input("1801", 10, 1800))
        self.assertIsNone(parse_bounded_integer_input("20.5", 5, 1000))

    def test_plot_downsampling_is_bounded_and_preserves_extrema(self):
        samples = [
            TransmissionSample(float(index), float(index), -float(index), 80.0)
            for index in range(10000)
        ]
        samples[4321] = TransmissionSample(4321.0, 50000.0, -4321.0, 250.0)
        reduced = downsample_transmission_samples(samples, 2000)
        self.assertLessEqual(len(reduced), 2000)
        self.assertEqual(reduced[0], samples[0])
        self.assertEqual(reduced[-1], samples[-1])
        self.assertIn(samples[4321], reduced)

        scalar = [(float(index), float(index)) for index in range(10000)]
        scalar[6789] = (6789.0, -1000.0)
        reduced_scalar = downsample_scalar_points(scalar, 2000)
        self.assertLessEqual(len(reduced_scalar), 2000)
        self.assertIn(scalar[6789], reduced_scalar)

    def test_monitor_store_preserves_value_across_disconnect(self):
        store = MonitorStore()
        store.update("ICT01", value=1.2, timestamp=10.0, connected=True, units="nC")
        store.set_connected("ICT01", False)
        sample = store.snapshot()["ICT01"]
        self.assertFalse(sample.connected)
        self.assertEqual(sample.value, 1.2)
        self.assertEqual(sample.units, "nC")

    def test_monitor_store_keeps_a_bounded_timestamp_queue_without_duplicates(self):
        store = MonitorStore(queue_size=3)
        for timestamp in (1.0, 1.0, 2.0, 3.0, 4.0):
            store.update("ICT01", value=timestamp, timestamp=timestamp)
        queued = store.queued_snapshot()["ICT01"]
        self.assertEqual([sample.timestamp for sample in queued], [2.0, 3.0, 4.0])
        store.clear_queues("ICT01")
        self.assertEqual(store.queued_snapshot()["ICT01"], ())


class CTMonitorPairingTests(unittest.TestCase):
    @staticmethod
    def _sample(value: float, timestamp: float, *, connected: bool = True, severity: int = 0):
        return SignalSample(value, timestamp, connected, severity=severity)

    def test_pairs_new_timestamps_once_and_scales_vm_coulombs(self):
        pairer = ShotPairer()
        samples = {
            "ICT01": self._sample(5.5e-10, 100.0),
            "ICT02": self._sample(4.4e-10, 100.05),
        }
        result = pairer.try_pair(
            samples,
            "ICT01",
            "ICT02",
            now=100.1,
            scale_to_display_unit=1e9,
            tolerance_s=0.2,
            stale_timeout_s=None,
            minimum_upstream_value=0.01,
        )
        self.assertEqual(result.status, "valid")
        self.assertAlmostEqual(result.sample.upstream_value, 0.55)
        self.assertAlmostEqual(result.sample.efficiency_percent, 80.0)
        repeated = pairer.try_pair(
            samples,
            "ICT01",
            "ICT02",
            now=100.2,
            scale_to_display_unit=1e9,
            tolerance_s=0.2,
            stale_timeout_s=None,
            minimum_upstream_value=0.01,
        )
        self.assertEqual(repeated.status, "waiting for paired update")

    def test_pairs_irfel_current_values_without_unit_scaling(self):
        result = ShotPairer().try_pair(
            {
                "ICT02": self._sample(0.080, 100.0),
                "ICT03": self._sample(0.060, 100.05),
            },
            "ICT02",
            "ICT03",
            now=100.1,
            scale_to_display_unit=1.0,
            tolerance_s=0.2,
            stale_timeout_s=3.0,
            minimum_upstream_value=0.001,
        )
        self.assertEqual(result.status, "valid")
        self.assertAlmostEqual(result.sample.upstream_value, 0.080)
        self.assertAlmostEqual(result.sample.downstream_value, 0.060)
        self.assertAlmostEqual(result.sample.efficiency_percent, 75.0)

    def test_rejects_timestamp_mismatch_stale_disconnect_and_alarm(self):
        defaults = dict(
            upstream_key="ICT01",
            downstream_key="ICT02",
            now=20.0,
            scale_to_display_unit=1.0,
            tolerance_s=0.2,
            stale_timeout_s=3.0,
            minimum_upstream_value=0.01,
        )
        pairer = ShotPairer()
        mismatch = pairer.try_pair(
            {"ICT01": self._sample(1.0, 19.0), "ICT02": self._sample(1.0, 19.5)},
            **defaults,
        )
        self.assertEqual(mismatch.status, "timestamp mismatch")
        stale = ShotPairer().try_pair(
            {"ICT01": self._sample(1.0, 10.0), "ICT02": self._sample(1.0, 10.1)},
            **defaults,
        )
        self.assertEqual(stale.status, "stale data")
        invalid_timestamp = ShotPairer().try_pair(
            {"ICT01": self._sample(1.0, float("nan")), "ICT02": self._sample(1.0, 19.0)},
            **defaults,
        )
        self.assertEqual(invalid_timestamp.status, "missing timestamp")
        disconnected = ShotPairer().try_pair(
            {"ICT01": self._sample(1.0, 19.0, connected=False), "ICT02": self._sample(1.0, 19.0)},
            **defaults,
        )
        self.assertEqual(disconnected.status, "PV disconnected")
        alarm = ShotPairer().try_pair(
            {"ICT01": self._sample(1.0, 19.0, severity=2), "ICT02": self._sample(1.0, 19.0)},
            **defaults,
        )
        self.assertEqual(alarm.status, "PV alarm")

    def test_batch_pairing_preserves_all_updates_between_gui_refreshes(self):
        store = MonitorStore(queue_size=64)
        for index in range(10):
            timestamp = 100.0 + index * 0.02
            store.update("ICT01", value=5.5e-10, timestamp=timestamp)
            store.update("ICT02", value=(4.4 + index * 0.01) * 1e-10, timestamp=timestamp + 0.01)

        pairer = ShotPairer()
        result = pairer.pair_queued(
            store.queued_snapshot(),
            store.snapshot(),
            "ICT01",
            "ICT02",
            now=100.3,
            scale_to_display_unit=1e9,
            tolerance_s=0.2,
            stale_timeout_s=None,
            minimum_upstream_value=0.01,
        )
        self.assertEqual(result.status, "valid")
        self.assertEqual(len(result.samples), 10)
        self.assertAlmostEqual(result.samples[0].efficiency_percent, 80.0)
        repeated = pairer.pair_queued(
            store.queued_snapshot(),
            store.snapshot(),
            "ICT01",
            "ICT02",
            now=100.4,
            scale_to_display_unit=1e9,
            tolerance_s=0.2,
            stale_timeout_s=None,
            minimum_upstream_value=0.01,
        )
        self.assertEqual(repeated.status, "waiting for paired update")
        self.assertEqual(repeated.samples, ())

    def test_batch_pairing_discards_only_unmatched_older_events(self):
        queues = {
            "ICT01": (
                self._sample(1.0, 1.0),
                self._sample(1.0, 2.0),
            ),
            "ICT02": (self._sample(0.5, 2.05),),
        }
        latest = {key: values[-1] for key, values in queues.items()}
        result = ShotPairer().pair_queued(
            queues,
            latest,
            "ICT01",
            "ICT02",
            now=2.1,
            scale_to_display_unit=1.0,
            tolerance_s=0.2,
            stale_timeout_s=None,
            minimum_upstream_value=0.01,
        )
        self.assertEqual(len(result.samples), 1)
        self.assertEqual(result.mismatched_samples, 1)
        self.assertAlmostEqual(result.samples[0].efficiency_percent, 50.0)


class CTMonitorProfileTests(unittest.TestCase):
    def test_half_profile_exposes_real_and_vm_ct_channels(self):
        real = load_app_context("ct_monitor", machine_id="half", control_backend="real")
        vm = load_app_context("ct_monitor", machine_id="half", control_backend="vm")
        self.assertEqual(resolve_channel(real, "FCT1", "peak_current"), "IN:BD:FCT1:I")
        self.assertEqual(resolve_channel(real, "ICT04", "charge"), "IN:BD:ICT4:C")
        self.assertEqual(resolve_channel(vm, "ICT01", "charge"), "HALF:IN:BD:ICT1:C:vm")
        with self.assertRaises(MachineProfileError):
            resolve_channel(vm, "FCT1", "peak_current")
        vm_cts = list_elements(vm, kind="ct", logical_channel="charge", control_backend="vm")
        self.assertEqual([element.id for element in vm_cts], ["ICT01", "ICT02", "ICT03", "ICT04"])
        raw_workflow = get_workflow(vm.profile, "ct_monitor")
        self.assertNotIn("label", raw_workflow["measurement"])
        workflow = resolve_ct_monitor_workflow(vm.profile)
        self.assertEqual(workflow["default_upstream"], "ICT01")
        self.assertEqual(workflow["default_downstream"], "ICT02")
        self.assertEqual(workflow["measurement_channel"], "charge")
        self.assertEqual(workflow["measurement_unit"], "nC")
        self.assertEqual(workflow["scale_to_display_unit"]["vm"], 1e9)
        self.assertEqual(workflow["event_queue_size"], 512)
        self.assertEqual(workflow["rolling_window_options"], [10, 20, 50, 100])
        self.assertEqual(workflow["rolling_window_input_range"], [5, 1000])
        self.assertEqual(workflow["trend_window_options_s"], [30, 60, 120, 300])
        self.assertEqual(workflow["trend_window_input_range_s"], [10, 5000])
        self.assertEqual(workflow["max_plot_points"], 2000)

    def test_irfel_real_exposes_three_current_icts_and_vm_is_unsupported(self):
        supported, reason = describe_app_support("irfel", "ct_monitor")
        self.assertTrue(supported, reason)

        real = load_app_context("ct_monitor", machine_id="irfel", control_backend="real")
        current_icts = list_elements(
            real,
            kind="ct",
            logical_channel="current",
            control_backend="real",
        )
        self.assertEqual(
            [element.id for element in current_icts],
            ["ICT02", "ICT03", "ICT04"],
        )
        self.assertEqual(
            resolve_channel(real, "ICT02", "current"),
            "IRFEL:BD:CT:CT2:I",
        )
        self.assertEqual(
            resolve_channel(real, "ICT03", "current"),
            "IRFEL:BD:CT:CT3:I",
        )
        self.assertEqual(
            resolve_channel(real, "ICT04", "current"),
            "IRFEL:BD:CT:CT4:I",
        )
        workflow = resolve_ct_monitor_workflow(real.profile)
        self.assertEqual(workflow["control_backends"], ["real"])
        self.assertEqual(workflow["default_upstream"], "ICT02")
        self.assertEqual(workflow["default_downstream"], "ICT03")
        self.assertEqual(workflow["measurement_channel"], "current")
        self.assertEqual(workflow["measurement_label"], "current")
        self.assertEqual(workflow["measurement_unit"], "A")
        self.assertEqual(workflow["minimum_upstream_value"], 0.001)

        with self.assertRaisesRegex(MachineProfileError, "does not support backend 'vm'"):
            load_app_context("ct_monitor", machine_id="irfel", control_backend="vm")

    def test_legacy_flat_workflow_remains_supported(self):
        legacy = {
            "control_backends": ["real"],
            "measurement_channel": "current",
            "measurement_label": "beam current",
            "measurement_unit": "A",
        }

        self.assertEqual(_normalize_ct_monitor_workflow(legacy), legacy)

    def test_structured_workflow_derives_measurement_label(self):
        normalized = _normalize_ct_monitor_workflow(
            {
                "control_backends": ["real"],
                "measurement": {
                    "channel": "current",
                    "unit": "A",
                    "scale_to_display_unit": {"real": 1},
                    "minimum_upstream_value": 0.001,
                },
                "default_pair": {"upstream": "ICT02", "downstream": "ICT03"},
                "acquisition": {},
                "rolling": {},
                "trend": {},
                "display": {},
            }
        )

        self.assertEqual(normalized["measurement_label"], "current")

    def test_vm_publish_plan_contains_four_charge_specs(self):
        plan = build_vm_publish_plan(load_profile("half"))
        self.assertEqual(
            [(spec.source_watch_id, spec.logical_channel) for spec in plan.watch_scalar_specs],
            [("ICT01", "charge"), ("ICT02", "charge"), ("ICT03", "charge"), ("ICT04", "charge")],
        )


class CTMonitorElegantTests(unittest.TestCase):
    def test_scalar_loader_reads_last_row_from_last_nonempty_page(self):
        dataset = type(
            "Dataset",
            (),
            {
                "columnName": ["Particles", "Charge"],
                "columnData": [[[10000], [9000]], [[5.5e-10], [4.95e-10]]],
                "load": lambda self, _path: None,
            },
        )()
        with patch(
            "half_linac.src.shared.elegant_backend.parser._new_legacy_sdds_dataset",
            return_value=dataset,
        ):
            value = _load_watch_scalar_from_sdds(Path("fake.sdds"), "Charge")
        self.assertEqual(value, 4.95e-10)

    def test_scalar_publisher_writes_value_and_invalidates_inactive_source(self):
        spec = VmWatchScalarPublishSpec("ICT01", "ICT01", "charge", "VM:ICT01:C", "Charge")
        plan = VmPublishPlan(watch_scalar_specs=(spec,))
        lattice = {"ICT01": {"TYPE": "WATCH", "MODE": "parameter", "DISABLE": "0"}}
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "ICT01.out"
            output.write_text("stub", encoding="utf-8")
            with patch(
                "half_linac.src.shared.elegant_backend.publisher._load_watch_scalar_from_sdds",
                return_value=5.5e-10,
            ), patch(
                "half_linac.src.shared.elegant_backend.publisher.caput",
                return_value=True,
            ) as caput_mock:
                ok = VmPublisher().publish_watch_scalars(
                    plan,
                    lattice=lattice,
                    usedline=["ICT01"],
                    elegant_dir=tmpdir,
                )
                self.assertTrue(ok)
                self.assertEqual(caput_mock.call_args.args[1], 5.5e-10)

            with patch(
                "half_linac.src.shared.elegant_backend.publisher.caput",
                return_value=True,
            ) as caput_mock:
                ok = VmPublisher().publish_watch_scalars(
                    plan,
                    lattice=lattice,
                    usedline=[],
                    elegant_dir=tmpdir,
                )
                self.assertTrue(ok)
                self.assertTrue(math.isnan(caput_mock.call_args.args[1]))

    def test_bootstrap_lattice_uses_parameter_watch_for_each_ict(self):
        parser = lattice_parser(
            str(REPO_ROOT / "src/virtual_machine/half_elegant/elegant/lattice_ini.lte"),
            "ALL_MAIN",
        )
        lattice, usedline = parser.get_lattice_tracklinenameslist()
        for element_id in ("ICT01", "ICT02", "ICT03", "ICT04"):
            self.assertEqual(lattice[element_id]["TYPE"], "WATCH")
            self.assertEqual(lattice[element_id]["MODE"], "parameter")
            self.assertEqual(lattice[element_id]["DISABLE"], "0")
            self.assertIn(element_id, usedline)

    def test_scalar_watch_reconciliation_preserves_other_runtime_settings(self):
        spec = VmWatchScalarPublishSpec("ICT01", "ICT01", "charge", "VM:ICT01:C", "Charge")
        runtime_state = {
            "control": {"run_control": {"n_steps": "7"}},
            "lattice": {
                "ICT01S": {"NAME": "ICT01S", "TYPE": "MARK"},
                "Q1": {"NAME": "Q1", "TYPE": "QUAD", "K1": "2.5"},
                "ALL_MAIN": {"NAME": "ALL_MAIN", "TYPE": "LINE", "LINE": "ICT01S,Q1"},
            },
            "usedline": ["ICT01S", "Q1"],
            "usedline_context": {"first": "ICT01S", "last": "Q1"},
        }
        bootstrap = {
            "ICT01": {
                "NAME": "ICT01",
                "TYPE": "WATCH",
                "FILENAME": "ICT01.out",
                "MODE": "parameter",
                "DISABLE": "0",
            }
        }
        changed = reconcile_watch_scalar_sources(runtime_state, bootstrap, (spec,))
        self.assertTrue(changed)
        self.assertNotIn("ICT01S", runtime_state["lattice"])
        self.assertEqual(runtime_state["usedline"], ["ICT01", "Q1"])
        self.assertEqual(runtime_state["lattice"]["ALL_MAIN"]["LINE"], "ICT01,Q1")
        self.assertEqual(runtime_state["lattice"]["Q1"]["K1"], "2.5")
        self.assertEqual(runtime_state["control"]["run_control"]["n_steps"], "7")
        self.assertFalse(reconcile_watch_scalar_sources(runtime_state, bootstrap, (spec,)))

    def test_generated_substitutions_include_ct_aliases(self):
        substitutions = (
            REPO_ROOT / "src/softIOC/halflinac/db/halflinac.substitutions"
        ).read_text(encoding="utf-8")
        self.assertIn("pattern {CT, RECORD, ALIAS}", substitutions)
        self.assertIn(
            '{ "ICT01", "VMIOC:CT:ICT01:CHARGE", "HALF:IN:BD:ICT1:C:vm" }',
            substitutions,
        )


if __name__ == "__main__":
    unittest.main()
