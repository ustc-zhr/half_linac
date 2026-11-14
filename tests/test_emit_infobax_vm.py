"""HALF VM adapter tests. No IOC, VM, elegant process, or real PV is used."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from repo_bootstrap import ensure_repo_import_path

REPO_ROOT = ensure_repo_import_path(__file__)

from half_linac.src.apps.emit_measure.infobax_vm import (  # noqa: E402
    FiniteWorkpointTable,
    HalfVmInfoBAXError,
    HalfVmInfoBAXMachine,
    HalfVmMeasurementError,
    HalfVmRestoreError,
    MachineVariable,
    build_half_vm_infobax_pool,
)
from half_linac.src.shared.machine_profile import load_app_context  # noqa: E402
from half_linac.src.virtual_machine.workbench_data import input_version, observation_dir
from half_linac.src.shared.machine_profile.resolver import (  # noqa: E402
    resolve_channel,
    resolve_write_target,
)
from half_linac.src.apps.emit_measure.infobax_session import InfoBAXSession, InfoBAXSessionConfig
from half_linac.src.optimization.infobax import InfoBAXCore, InfoBAXConfig


def workpoints(element="QT01", values=(-0.5, 0.5), *, quantity="K1", unit="1/m^2"):
    return FiniteWorkpointTable(
        candidate_ids=tuple(f"wp-{index}" for index in range(len(values))),
        variables=(MachineVariable(element, quantity, unit, 1.0e-7),),
        values=np.asarray(values, dtype=float)[:, None],
    )


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = lambda: None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.on_sleep()


class FakePvIo:
    def __init__(self, context):
        self.work_pv = resolve_write_target(
            context, "QT01", quantity="K1", unit="1/m^2", mode="vm"
        ).pv_name
        self.scan_pv = resolve_write_target(
            context, "QT02", quantity="K1", unit="1/m^2", mode="vm"
        ).pv_name
        self.image_pv = resolve_channel(context, "PRF07", "image", mode="vm")
        self.values = {self.work_pv: 0.1, self.scan_pv: -0.2, self.image_pv: [1.0]}
        self.writes = []
        self.fail_pv = None
        self.clock = FakeClock()
        self.temporary = TemporaryDirectory()
        self.runtime_json = Path(self.temporary.name) / "machine.json"
        self.directory = observation_dir(self.runtime_json)
        self.directory.mkdir()
        self.state = {
            "usedline": ["QT01", "QT02", "PRF07"],
            "lattice": {
                "QT01": {"TYPE": "QUAD", "K1": 0.1},
                "QT02": {"TYPE": "QUAD", "K1": -0.2},
                "PRF07": {"TYPE": "WATCH", "MODE": "coord", "DISABLE": "0"},
            },
        }
        self.calculation = 0
        self.auto_publish = True
        self.image_reads = 0
        self.runtime_json.write_text(json.dumps(self.state))
        self.publish()

    def publish(self, *, screens=True, phase="Ready"):
        self.calculation += 1
        version = input_version(self.state)
        status = {"session": "fake-vm", "calculation": self.calculation,
                  "input_version": version, "result_version": version,
                  "phase": phase,
                  "publication": {"screens": screens, "screens_acknowledged": screens,
                                  "screen_image_pvs": [self.image_pv] if screens else []}}
        result = {"session": "fake-vm", "calculation": self.calculation,
                  "input_version": version, "input_state": self.state}
        (self.directory / "result.json").write_text(json.dumps(result))
        (self.directory / "status.json").write_text(json.dumps(status))

    def get(self, pv_name):
        if pv_name == self.image_pv:
            self.image_reads += 1
        return self.values.get(pv_name)

    def put(self, pv_name, value):
        self.writes.append((pv_name, value))
        if pv_name == self.fail_pv:
            raise RuntimeError("injected write failure")
        self.values[pv_name] = float(value)
        element = "QT01" if pv_name == self.work_pv else "QT02"
        self.state["lattice"][element]["K1"] = float(value)
        self.runtime_json.write_text(json.dumps(self.state))
        if self.auto_publish:
            self.publish()
        return True


class FakeModelBackend:
    def __init__(self):
        self.calls = []

    def get_map(self, elem1, elem2, **kwargs):
        self.calls.append((elem1, elem2, kwargs))
        matrix = np.eye(4)
        matrix[0, 1] = kwargs["k1"]
        matrix[2, 3] = 2.0 * kwargs["k1"]
        return matrix


class HalfVmInfoBAXTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = load_app_context(
            "emit_measure", machine_id="half", control_backend="vm"
        )

    def machine(self, io=None, **kwargs):
        io = io or FakePvIo(self.context)
        self.addCleanup(io.temporary.cleanup)
        machine = HalfVmInfoBAXMachine(
            self.context,
            workpoints(),
            scan_quad="QT02",
            screen="PRF07",
            plane="x",
            get=io.get,
            put=io.put,
            runtime_json=io.runtime_json,
            clock=io.clock,
            sleeper=io.clock.sleep,
            measurement_timeout=0.2,
            **kwargs,
        )
        machine.validate_pool(build_half_vm_infobax_pool(
            self.context, workpoints(), scan_quad="QT02", screen="PRF07",
            plane="x", scan_values=(-1.0, 0.0, 0.75), energy_mev=225,
            observation_noise_std_mm2=0.03, model_backend=FakeModelBackend(),
        ))
        return machine, io

    def test_real_backend_is_rejected(self):
        context = load_app_context(
            "emit_measure", machine_id="half", control_backend="real"
        )
        with self.assertRaisesRegex(HalfVmInfoBAXError, "only backend 'vm'"):
            HalfVmInfoBAXMachine(
                context,
                workpoints(),
                scan_quad="QT02",
                screen="PRF07",
                plane="x",
                get=lambda _pv: 0.0,
                put=lambda _pv, _value: True,
            )

    def test_non_half_machine_is_rejected(self):
        machine_config = replace(self.context.profile.machine, id="not-half")
        profile = replace(self.context.profile, machine=machine_config)
        context = replace(self.context, profile=profile)
        with self.assertRaisesRegex(HalfVmInfoBAXError, "only machine 'half'"):
            HalfVmInfoBAXMachine(
                context,
                workpoints(),
                scan_quad="QT02",
                screen="PRF07",
                plane="x",
                get=lambda _pv: 0.0,
                put=lambda _pv, _value: True,
            )

    def test_missing_vm_workpoint_channel_is_rejected(self):
        table = workpoints("SS01", (1.0, 2.0), quantity="current", unit="A")
        with self.assertRaises(Exception):
            HalfVmInfoBAXMachine(
                self.context,
                table,
                scan_quad="QT02",
                screen="PRF07",
                plane="x",
                get=lambda _pv: 0.0,
                put=lambda _pv, _value: True,
            )

    def test_upstream_quad_k1_and_prf_image_resolve(self):
        machine, io = self.machine()
        self.assertEqual(machine.candidate_ids, ("wp-0", "wp-1"))
        self.assertIn("QT01", io.work_pv)
        self.assertIn("QT02", io.scan_pv)
        self.assertIn("PRF07", io.image_pv)

    def test_out_of_limit_workpoint_is_rejected(self):
        original = self.context.profile.get_element("QT01")
        limits = dict(original.limits)
        limits["K1"] = {"low": -1.0, "high": 1.0, "unit": "1/m^2"}
        updated = replace(original, limits=limits)
        elements = tuple(
            updated if element.id == updated.id else element
            for element in self.context.profile.elements
        )
        by_id = dict(self.context.profile._elements_by_id)
        by_id[updated.id] = updated
        profile = replace(
            self.context.profile, elements=elements, _elements_by_id=by_id
        )
        context = replace(self.context, profile=profile)
        with self.assertRaisesRegex(HalfVmInfoBAXError, "outside"):
            HalfVmInfoBAXMachine(
                context,
                workpoints(values=(-2.0, 0.0)),
                scan_quad="QT02",
                screen="PRF07",
                plane="x",
                get=lambda _pv: 0.0,
                put=lambda _pv, _value: True,
            )

    def test_scan_quad_cannot_be_workpoint_variable(self):
        with self.assertRaisesRegex(HalfVmInfoBAXError, "cannot also"):
            HalfVmInfoBAXMachine(
                self.context,
                workpoints("QT02"),
                scan_quad="QT02",
                screen="PRF07",
                plane="x",
                get=lambda _pv: 0.0,
                put=lambda _pv, _value: True,
            )

    def test_downstream_workpoint_is_rejected_before_model_calls(self):
        backend = FakeModelBackend()
        with self.assertRaisesRegex(HalfVmInfoBAXError, "upstream"):
            build_half_vm_infobax_pool(
                self.context, workpoints("QT02"), scan_quad="QT01", screen="PRF07",
                scan_values=(-1, 0, 1), plane="x", energy_mev=225,
                observation_noise_std_mm2=0.03, model_backend=backend,
            )
        self.assertEqual(backend.calls, [])

    def test_pool_binds_physical_workpoints_and_measurement_plane(self):
        machine, io = self.machine()
        for table, screen, plane in (
            (workpoints(), "PRF07", "y"),
            (workpoints(), "PRF08", "x"),
            (workpoints(values=(-1.0, 1.0)), "PRF07", "x"),
        ):
            pool = build_half_vm_infobax_pool(
                self.context, table, scan_quad="QT02", screen=screen,
                scan_values=(-1, 0, 1), plane=plane, energy_mev=225,
                observation_noise_std_mm2=0.03, model_backend=FakeModelBackend(),
            )
            with self.subTest(screen=screen, plane=plane, values=table.values.tolist()):
                with self.assertRaisesRegex(HalfVmInfoBAXError, "contract"):
                    machine.validate_pool(pool)
                self.assertEqual(io.writes, [])
        with self.assertRaisesRegex(HalfVmInfoBAXError, "contract"):
            machine.validate_pool(replace(machine._pool, measurement_contract_fingerprint=None))

    def test_repeated_vm_frame_sampling_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "one frame per input"):
            self.machine(image_samples=2)

    def test_out_of_pool_scan_and_wrong_query_index_are_rejected(self):
        machine, io = self.machine()
        with self.assertRaisesRegex(HalfVmInfoBAXError, "member"):
            machine.apply_query(0, 0.123)
        self.assertEqual(io.writes, [])
        machine.apply_query(0, -1.0)
        with self.assertRaisesRegex(HalfVmMeasurementError, "does not match"):
            machine.measure_point(1, 0.03)
        self.assertEqual(io.image_reads, 0)

    def valid_machine(self):
        fit = SimpleNamespace(valid=True, status="valid", sigx_mm=1.25, sigy_mm=2.0)
        return self.machine(analyzer=lambda _raw, **_kw: (None, fit))

    def test_stale_vm_result_is_never_read_as_a_new_query(self):
        machine, io = self.valid_machine()
        io.auto_publish = False
        machine.apply_query(0, -1.0)
        with self.assertRaisesRegex(HalfVmMeasurementError, "timeout"):
            machine.measure_point(0, 0.03)
        self.assertEqual(io.image_reads, 0)

    def test_delayed_matching_vm_publication_is_accepted_and_audited(self):
        machine, io = self.valid_machine()
        io.auto_publish = False
        machine.apply_query(0, -1.0)
        io.clock.on_sleep = io.publish
        measurement = machine.measure_point(0, 0.03)
        self.assertGreater(io.clock.now, 0)
        self.assertEqual(io.image_reads, 1)
        self.assertEqual(measurement.metadata["vm_input_version"], input_version(io.state))
        self.assertEqual(measurement.metadata["vm_calculation"], io.calculation)
        with self.assertRaisesRegex(HalfVmMeasurementError, "before measuring"):
            machine.measure_point(0, 0.03)

    def test_failed_publication_and_failed_calculation_are_rejected(self):
        for screens, phase, message in (
            (False, "Ready", "publication"), (True, "Failed", "calculation failed"),
        ):
            with self.subTest(phase=phase, screens=screens):
                machine, io = self.valid_machine()
                machine.apply_query(0, -1.0)
                io.publish(screens=screens, phase=phase)
                with self.assertRaisesRegex(HalfVmMeasurementError, message):
                    machine.measure_point(0, 0.03)
                self.assertEqual(io.image_reads, 0)

    def test_image_read_crossing_an_input_change_is_not_committed(self):
        machine, io = self.valid_machine()
        machine.apply_query(0, -1.0)
        original_get = io.get

        def drifting_get(pv):
            value = original_get(pv)
            if pv == io.image_pv:
                io.put(io.scan_pv, 0.0)
            return value

        machine._get = drifting_get
        with self.assertRaisesRegex(HalfVmMeasurementError, "timeout"):
            machine.measure_point(0, 0.03)
        self.assertEqual(io.image_reads, 1)

    def test_vm_runtime_topology_is_checked_independently_of_profile(self):
        machine, io = self.valid_machine()
        machine.apply_query(0, -1.0)
        io.state["usedline"] = ["QT02", "QT01", "PRF07"]
        io.runtime_json.write_text(json.dumps(io.state))
        io.publish()
        with self.assertRaisesRegex(HalfVmMeasurementError, "upstream"):
            machine.measure_point(0, 0.03)

    def test_result_input_hash_is_verified(self):
        machine, io = self.valid_machine()
        machine.apply_query(0, -1.0)
        path = io.directory / "result.json"
        result = json.loads(path.read_text())
        result["input_state"]["lattice"]["QT02"]["K1"] = 123.0
        path.write_text(json.dumps(result))
        with self.assertRaisesRegex(HalfVmMeasurementError, "timeout"):
            machine.measure_point(0, 0.03)
        self.assertEqual(io.image_reads, 0)

    def test_older_vm_without_acknowledged_images_is_rejected(self):
        machine, io = self.valid_machine()
        machine.apply_query(0, -1.0)
        path = io.directory / "status.json"
        status = json.loads(path.read_text())
        del status["publication"]["screens_acknowledged"]
        path.write_text(json.dumps(status))
        with self.assertRaisesRegex(HalfVmMeasurementError, "restart"):
            machine.measure_point(0, 0.03)
        self.assertEqual(io.image_reads, 0)

    def test_other_screen_acknowledgement_does_not_authorize_this_image(self):
        machine, io = self.valid_machine()
        machine.apply_query(0, -1.0)
        path = io.directory / "status.json"
        status = json.loads(path.read_text())
        status["publication"]["screen_image_pvs"] = ["OTHER:SCREEN"]
        path.write_text(json.dumps(status))
        with self.assertRaisesRegex(HalfVmMeasurementError, "requested screen"):
            machine.measure_point(0, 0.03)
        self.assertEqual(io.image_reads, 0)

    def test_session_end_to_end_uses_matching_images_and_restores(self):
        table = workpoints(values=(-0.5, 0.0, 0.5))
        pool = build_half_vm_infobax_pool(
            self.context, table, scan_quad="QT02", screen="PRF07", plane="x",
            scan_values=(-1.0, 0.0, 1.0), energy_mev=225,
            observation_noise_std_mm2=0.03, model_backend=FakeModelBackend(),
        )
        io = FakePvIo(self.context)
        self.addCleanup(io.temporary.cleanup)
        io.auto_publish = False
        machine = HalfVmInfoBAXMachine(
            self.context, table, scan_quad="QT02", screen="PRF07", plane="x",
            get=io.get, put=io.put, runtime_json=io.runtime_json,
            clock=io.clock, sleeper=io.clock.sleep, measurement_timeout=0.2,
        )
        width, height = machine._geometry.shape
        pixel = machine._geometry.pixel_width_mm
        x = np.linspace(-width * pixel / 2, width * pixel / 2, width) - 0.1
        y = np.linspace(-height * pixel / 2, height * pixel / 2, height) - 0.1
        expected_variances = {}

        def publish_image():
            work = io.values[io.work_pv]
            scan = io.values[io.scan_pv]
            moments = np.array((0.36 + 0.2 * work**2, 0.02, 0.09))
            variance = float(np.array((1, 2 * scan, scan**2)) @ moments)
            io.values[io.image_pv] = (
                1000 * np.exp(-0.5 * (x[None, :]**2 / variance + y[:, None]**2 / 0.36))
            ).ravel()
            io.publish()
            expected_variances[input_version(io.state)] = variance

        io.clock.on_sleep = publish_image
        core = InfoBAXCore(pool, InfoBAXConfig(seed=0))
        initial = machine.snapshot()
        with TemporaryDirectory() as directory:
            session = InfoBAXSession(core, machine, directory,
                                    InfoBAXSessionConfig(max_observations=7))
            session.run()
            self.assertEqual(session.status, "completed")
            self.assertEqual(len(core.observations), 7)
            self.assertEqual(session.restoration_status, "succeeded")
            self.assertEqual(machine.snapshot(), initial)
            self.assertEqual(io.image_reads, 7)
            for record in session.records:
                measurement = record["measurement"]
                metadata = measurement["metadata"]
                expected = expected_variances[metadata["vm_input_version"]]
                self.assertAlmostEqual(measurement["variance_mm2"], expected, places=5)
            loaded = InfoBAXSession.from_run_dir(pool, machine, directory)
            self.assertEqual(loaded.core.to_checkpoint(), core.to_checkpoint())

    def test_workpoint_shape_and_ids_are_validated(self):
        with self.assertRaisesRegex(ValueError, "shape"):
            FiniteWorkpointTable(
                candidate_ids=("a", "b"),
                variables=(MachineVariable("QT01"),),
                values=((0.0,),),
            )
        with self.assertRaisesRegex(ValueError, "unique"):
            FiniteWorkpointTable(
                candidate_ids=("a", "a"),
                variables=(MachineVariable("QT01"),),
                values=((0.0,), (1.0,)),
            )

    def test_apply_query_writes_workpoint_then_scan_and_verifies(self):
        machine, io = self.machine()
        machine.apply_query(1, 0.75)
        self.assertEqual(
            io.writes,
            [(io.work_pv, 0.5), (io.scan_pv, 0.75)],
        )
        self.assertEqual(io.values[io.work_pv], 0.5)
        self.assertEqual(io.values[io.scan_pv], 0.75)

    def test_image_fit_sigma_is_converted_to_variance(self):
        fit = SimpleNamespace(
            valid=True,
            status="valid",
            message="",
            sigx_mm=1.25,
            sigy_mm=2.0,
        )

        def analyzer(_raw, **_kwargs):
            return np.zeros((1, 1)), fit

        machine, _io = self.machine(analyzer=analyzer)
        machine.apply_query(1, 0.0)
        measurement = machine.measure_point(4, 0.03)
        self.assertAlmostEqual(measurement.variance_mm2, 1.25**2)
        self.assertEqual(measurement.noise_std_mm2, 0.03)
        self.assertEqual(measurement.metadata["query_index"], 4)

    def test_missing_and_invalid_images_fail_explicitly(self):
        machine, io = self.machine()
        machine.apply_query(0, -1.0)
        io.values[io.image_pv] = None
        with self.assertRaisesRegex(HalfVmMeasurementError, "failed to read"):
            machine.measure_point(0, 0.03)

        io.values[io.image_pv] = [1.0]
        with self.assertRaisesRegex(HalfVmMeasurementError, "image size"):
            machine.measure_point(0, 0.03)

    def test_invalid_fit_is_rejected(self):
        fit = SimpleNamespace(
            valid=False,
            status="fit_failed",
            message="no signal",
            sigx_mm=None,
            sigy_mm=None,
        )
        machine, _io = self.machine(analyzer=lambda _raw, **_kw: (None, fit))
        machine.apply_query(0, -1.0)
        with self.assertRaisesRegex(HalfVmMeasurementError, "fit_failed"):
            machine.measure_point(0, 0.03)

    def test_restore_attempts_every_channel(self):
        machine, io = self.machine()
        snapshot = machine.snapshot()
        io.fail_pv = io.work_pv
        with self.assertRaises(HalfVmRestoreError):
            machine.restore(snapshot)
        self.assertEqual(
            [pv for pv, _value in io.writes],
            [io.work_pv, io.scan_pv],
        )

    def test_model_builder_generates_expected_design_matrix(self):
        backend = FakeModelBackend()
        table = workpoints()
        pool = build_half_vm_infobax_pool(
            self.context,
            table,
            scan_quad="QT02",
            screen="PRF07",
            scan_values=(-1.0, 0.0, 1.0),
            plane="x",
            energy_mev=225.0,
            observation_noise_std_mm2=0.02,
            model_backend=backend,
        )
        np.testing.assert_allclose(
            pool.design_matrix,
            ((1.0, -2.0, 1.0), (1.0, 0.0, 0.0), (1.0, 2.0, 1.0)),
        )
        self.assertEqual(pool.candidate_ids, table.candidate_ids)
        self.assertEqual(len(backend.calls), 3)
        self.assertTrue(
            all(call[2]["seq"] == "ent2exit" for call in backend.calls)
        )
        self.assertTrue(all(call[2]["twiss_only"] for call in backend.calls))

    def test_module_import_has_no_epics_gui_or_research_side_effect(self):
        code = """
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path('tests/test_emit_infobax_vm.py')
import sys
from half_linac.src.apps.emit_measure import infobax_vm
blocked = [name for name in sys.modules if name == 'epics' or name.startswith('PyQt5') or name == 'GOTAcc' or name.startswith('research')]
raise SystemExit(1 if blocked else 0)
"""
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "tests")
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
