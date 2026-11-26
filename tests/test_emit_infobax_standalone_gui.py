"""Offscreen standalone GUI tests: approvals and close are real Qt events."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
os.environ.setdefault("MPLCONFIGDIR","/tmp/emit-infobax-gui-mpl")
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from PyQt5.QtWidgets import QApplication
from half_linac.src.apps.emit_measure.infobax_gui import InfoBAXWindow


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.read_patch=patch("epics.caget",side_effect=AssertionError("Live read forbidden"))
        self.write_patch=patch("epics.caput",side_effect=AssertionError("Live write forbidden"))
        self.read_patch.start(); self.write_patch.start(); self.addCleanup(patch.stopall)
        self.window=InfoBAXWindow(run_root=self.temp.name)
        self.addCleanup(self.cleanup)
        self.window.active.setValue(0)
        self.window.scan_low.setValue(-1); self.window.scan_high.setValue(1); self.window.scan_points.setValue(5)

    def pump(self,predicate,timeout=10):
        deadline=time.monotonic()+timeout
        while not predicate() and time.monotonic()<deadline:
            self.app.processEvents(); time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(predicate(),self.window.status.text())

    def cleanup(self):
        if self.window.busy():
            self.window.stop_run(False); self.pump(lambda:not self.window.busy())
        self.window.close()

    def test_open_is_passive_and_real_uses_the_framework(self):
        self.assertEqual(self.window.backend.currentText(),"fake")
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertFalse(self.window.approve_button.isEnabled())
        self.window.backend.setCurrentText("real")
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertIn("标签页",self.window.note.text())

    def test_pause_pending_then_resume_requires_new_approval(self):
        self.window.start_run()
        self.pump(lambda:self.window.approve_button.isEnabled())
        self.assertEqual(self.window.workflow.machine.moves,[])
        self.window.stop_run(True)
        self.pump(lambda:not self.window.busy())
        self.assertEqual(self.window.workflow.status,"paused")
        self.assertFalse(self.window.approve_button.isEnabled())
        self.window.start_run()
        self.pump(lambda:self.window.approve_button.isEnabled())
        self.assertEqual(self.window.workflow.status,"awaiting_approval")
        self.assertFalse(any(m[0]!="restore" for m in self.window.workflow.machine.moves))
        self.window.stop_run(False)
        self.pump(lambda:not self.window.busy())
        self.assertEqual(self.window.workflow.status,"stopped")

    def test_all_approvals_complete_restore_and_present_metrics(self):
        self.window.start_run()
        deadline=time.monotonic()+20
        approvals=0
        while self.window.busy() and time.monotonic()<deadline:
            self.app.processEvents()
            if self.window.approve_button.isEnabled():
                approvals+=1; self.window.approve_current()
            time.sleep(.005)
        self.pump(lambda:not self.window.busy())
        self.assertEqual(self.window.workflow.status,"completed")
        self.assertEqual(approvals,8)
        self.assertEqual(self.window.workflow.machine.current,0.)
        self.assertTrue(self.window.apply_button.isEnabled())
        self.assertIn("mm·mrad",self.window.summary.text())
        self.assertEqual(self.window.table.rowCount(),16)

    def test_close_pending_cancels_and_restores_before_exit(self):
        self.window.start_run()
        self.pump(lambda:self.window.approve_button.isEnabled())
        self.window.close()
        self.pump(lambda:not self.window.busy())
        self.assertEqual(self.window.workflow.status,"stopped")
        self.assertEqual(self.window.workflow.restoration_status,"succeeded")



class LeaseTests(unittest.TestCase):
    def test_ordinary_scan_conflicts_and_nested_scan_borrows_held_lease(self):
        import sys
        from pathlib import Path
        with patch.object(sys, "path", [str(Path(__file__).resolve().parents[1]/"src/apps/emit_measure"), *sys.path]):
            from half_linac.src.apps.emit_measure.main import scanThread
        from half_linac.src.apps.emit_measure.control import emittance_lock
        from half_linac.src.shared.machine_profile import load_app_context
        from PyQt5.QtCore import QThread
        scan=scanThread.__new__(scanThread)
        QThread.__init__(scan)
        scan.app_context=load_app_context("emit_measure",machine_id="half",control_backend="real")
        scan.recal=False; scan.control_lease=None; scan.terminal_result={}
        with patch.object(scan,"_run_scan_locked") as run:
            with emittance_lock("half","real") as lease:
                scan.run()
                run.assert_not_called()
                self.assertTrue(scan.terminal_result["fatal"])
                scan.control_lease=lease
                scan.run()
                run.assert_called_once()
                self.assertIsNotNone(lease._stream)
        scan.control_lease=lease
        with patch.object(scan,"_run_scan_locked") as run:
            scan.run()
            run.assert_not_called()

    def test_existing_optimization_holds_the_same_lease(self):
        from types import SimpleNamespace
        from half_linac.src.apps.emit_measure.optimization import OptimizationSession
        from half_linac.src.apps.emit_measure.control import emittance_lock
        from half_linac.src.shared.machine_profile import load_app_context
        from half_linac.src.shared.machine_profile.control_lock import ControlLockError
        session=object.__new__(OptimizationSession)
        session.device=SimpleNamespace(context=load_app_context("emit_measure",machine_id="half",control_backend="real"))
        def inside():
            with self.assertRaises(ControlLockError):
                with emittance_lock("half","real"): pass
            return "held"
        session._run_locked=inside
        self.assertEqual(session.run(),"held")

if __name__=="__main__": unittest.main()
