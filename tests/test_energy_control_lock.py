from __future__ import annotations

import json
import sys
import unittest
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.dispersion_correction.machine.epics import EpicsMachine
from half_linac.src.apps.dispersion_correction.workflow import energy_control_session
from half_linac.src.shared.machine_profile.control_lock import (
    ControlLockError,
    TimingControlLock,
    EnergyControlLock,
)
class EnergyControlLockTests(unittest.TestCase):
    def test_exclusive(self):
        import tempfile
        from pathlib import Path
        tmp_path = Path(tempfile.mkdtemp())
        first = EnergyControlLock(tmp_path / "energy.lock", {"app": "first"})
        second = EnergyControlLock(tmp_path / "energy.lock", {"app": "second"})
        first.acquire()
        try:
            with self.assertRaises(ControlLockError):
                second.acquire()
            owner = json.loads((tmp_path / "energy.lock").read_text())
            assert owner["app"] == "first"
        finally:
            first.release()
        second.acquire()
        second.release()

    def test_hv_feedback_and_dispersion_scan_contend_for_machine_lock(self):
        machine_id = f"unit-test-energy-{uuid4().hex}"
        hv_session = EnergyControlLock.for_machine(
            machine_id, {"app": "hv_feedback", "operation": "feedback"}
        )
        dispersion_scan = EnergyControlLock.for_machine(
            machine_id, {"app": "dispersion_correction", "operation": "energy_scan"}
        )
        hv_session.acquire()
        try:
            with self.assertRaises(ControlLockError):
                dispersion_scan.acquire()
        finally:
            hv_session.release()

    def test_dispersion_decorator_holds_lock_for_complete_operation(self):
        machine_id = f"unit-test-dispersion-{uuid4().hex}"

        class DummyWorkflow:
            def __init__(self):
                self.machine = object.__new__(EpicsMachine)
                self.config = SimpleNamespace(
                    backend=SimpleNamespace(options={"site": machine_id})
                )
                self._energy_control_lock_depth = 0

            @energy_control_session("energy_scan")
            def run_scan(self):
                competitor = EnergyControlLock.for_machine(
                    machine_id, {"app": "energy_feedback"}
                )
                try:
                    competitor.acquire()
                except ControlLockError:
                    return True
                competitor.release()
                return False

        workflow = DummyWorkflow()
        self.assertTrue(workflow.run_scan())
        after = EnergyControlLock.for_machine(machine_id, {"app": "after"})
        after.acquire()
        after.release()

    def test_context_release(self):
        import tempfile
        from pathlib import Path
        tmp_path = Path(tempfile.mkdtemp())
        path = tmp_path / "energy.lock"
        with EnergyControlLock(path, {"operation": "scan"}):
            assert path.exists()
        other = EnergyControlLock(path, {"operation": "next"})
        other.acquire()
        other.release()

    def test_timing_lock_is_machine_wide(self):
        machine_id = "unit-test-timing-lock"
        first = TimingControlLock.for_machine(
            machine_id, {"operation": "linked scan"}
        )
        second = TimingControlLock.for_machine(
            machine_id, {"operation": "manual restore"}
        )
        self.assertEqual(first.path, second.path)
        self.assertNotEqual(
            first.path,
            EnergyControlLock.for_machine(machine_id, {}).path,
        )
        first.acquire()
        try:
            with self.assertRaises(ControlLockError):
                second.acquire()
        finally:
            first.release()
