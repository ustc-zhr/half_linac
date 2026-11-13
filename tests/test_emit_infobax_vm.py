"""HALF VM adapter tests. No IOC, VM, elegant process, or real PV is used."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
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
from half_linac.src.shared.machine_profile.resolver import (  # noqa: E402
    resolve_channel,
    resolve_write_target,
)


def workpoints(element="QT01", values=(-0.5, 0.5), *, quantity="K1", unit="1/m^2"):
    return FiniteWorkpointTable(
        candidate_ids=tuple(f"wp-{index}" for index in range(len(values))),
        variables=(MachineVariable(element, quantity, unit, 1.0e-7),),
        values=np.asarray(values, dtype=float)[:, None],
    )


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

    def get(self, pv_name):
        return self.values.get(pv_name)

    def put(self, pv_name, value):
        self.writes.append((pv_name, value))
        if pv_name == self.fail_pv:
            raise RuntimeError("injected write failure")
        self.values[pv_name] = float(value)
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
        machine = HalfVmInfoBAXMachine(
            self.context,
            workpoints(),
            scan_quad="QT02",
            screen="PRF07",
            plane="x",
            get=io.get,
            put=io.put,
            **kwargs,
        )
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
        measurement = machine.measure_point(4, 0.03)
        self.assertAlmostEqual(measurement.variance_mm2, 1.25**2)
        self.assertEqual(measurement.noise_std_mm2, 0.03)
        self.assertEqual(measurement.metadata["query_index"], 4)

    def test_missing_and_invalid_images_fail_explicitly(self):
        machine, io = self.machine()
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
