"""Offscreen workspace regression tests. All control access is forbidden."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/emittance_matching_test_mpl")
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from half_linac.src.shared.machine_profile import load_app_context, resolve_write_target
from half_linac.src.apps.emit_measure.matching import Point, Twiss, MeasurementBaseline, MatchingResult
from half_linac.src.apps.emit_measure.matching_workspace import MatchingWorkspace
from half_linac.src.apps.emit_measure.matching_import import import_measurement


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = load_app_context("emit_measure", machine_id="half", control_backend="vm")
        self.writes = patch("epics.caput", side_effect=AssertionError("PV write forbidden"))
        self.reads = patch("epics.caget", side_effect=AssertionError("Automatic PV read forbidden"))
        self.writes.start(); self.reads.start()
        self.workspace = MatchingWorkspace(self.context)

    def tearDown(self):
        self.workspace.timer.stop(); self.workspace.close()
        self.writes.stop(); self.reads.stop()

    def test_construct_groups_and_manual_si_input(self):
        w = self.workspace
        self.assertIsNotNone(w.model)
        self.assertEqual(w.line.currentData(), "ALL_MAIN")
        self.assertEqual(w.presets.count(), 2)
        baseline = MeasurementBaseline(Point("QL09"), 114.15,
            {p: Twiss(3, 0.2, 2e-8) for p in ("x", "y")}, "half", "vm", "ALL_MAIN", {},
            {"kind": "manual"}, True)
        w.populate(baseline)
        with self.assertRaisesRegex(ValueError, "Declare"): w.measurement()
        w.declaration.setChecked(True)
        self.assertAlmostEqual(w.measurement().planes["x"].emittance, 2e-8)
        w.select_group()
        self.assertEqual(w.target.currentText(), "QL12")
        selected = [w.table.item(r, 1).text() for r in range(w.table.rowCount())
                    if w.table.item(r, 0).checkState() == Qt.Checked]
        self.assertEqual(selected, [f"QL{i:02d}" for i in range(7, 13)])
        self.assertTrue(all(not w.table.item(r, 3).text() for r in range(w.table.rowCount())))
        w.stale = False; w.energy.setText("115")
        self.assertTrue(w.stale)

    def test_snapshot_identity_rejected_without_reading_pvs(self):
        w = self.workspace
        with patch.object(w, "error") as error:
            w.apply_snapshot({"machine_id": "irfel", "control_backend": "real", "fields": []})
            error.assert_called_once()
        w.apply_snapshot({"machine_id": "half", "control_backend": "vm", "fields": [
            {"element_id": "QL07", "field_name": "K1", "value": 2}]})
        row = next(r for r in range(w.table.rowCount()) if w.table.item(r, 1).text() == "QL07")
        self.assertEqual(w.table.item(row, 2).text(), "2.0")
        self.assertFalse(w.declaration.isChecked())

    def test_missing_limits_identify_and_focus_cell(self):
        w = self.workspace
        w.select_group()
        selected = [r for r in range(w.table.rowCount())
                    if w.table.item(r, 0).checkState() == Qt.Checked]
        for row in selected:
            w.table.item(row, 2).setText("1")
        with patch.object(w, "error") as error, patch.object(w, "run_task") as run:
            w.calculate_match()
            message = str(error.call_args.args[0])
            self.assertIn("QL07: Lower", message)
            self.assertIn("QL07: Upper", message)
            self.assertIn("QL07: Max |ΔK1|", message)
            run.assert_not_called()
        self.assertEqual(w.table.currentRow(), selected[0])
        self.assertEqual(w.table.currentColumn(), 3)
        for row in selected:
            for column, value in ((3, "-2"), (4, "2"), (5, "0.2")):
                w.table.item(row, column).setText(value)
        self.assertEqual(len(w.selected_magnet_limits()), 6)
        w.table.item(selected[0], 5).setText("nan")
        with self.assertRaisesRegex(ValueError, "finite number"):
            w.selected_magnet_limits()

    def test_multi_screen_import_edge_and_uncertainty(self):
        payload = {"schema": "emit_multi_screen_v1", "machine": "half", "backend": "vm",
            "model_line": "ALL_MAIN", "reference_element": "PRF06", "energy_mev": 2200,
            "reconstruction": {p: {"status": "valid", "beta_m": 3, "alpha": 0.1,
                "geometric_emittance_m_rad": 2e-9, "geometric_emittance_standard_deviation_m_rad": 1e-10}
                               for p in ("x", "y")}}
        measurement = import_measurement(payload)
        self.assertEqual(measurement.point.edge, "exit")
        w = self.workspace
        w.populate(measurement); w.declaration.setChecked(True)
        self.assertIn("uncertainty", w.measurement().provenance)
        w.edits["x", "beta"].setText("4")
        self.assertNotIn("uncertainty", w.measurement().provenance)

    def test_apply_restore_buttons_and_durable_originals(self):
        w = self.workspace
        names = ["QL07", "QL08"]
        originals = {q: float(w.model.elements[q]["K1"]) for q in names}
        baseline = MeasurementBaseline(Point("QL07"), 114.15,
            {p: Twiss(3, 0.2, 2e-8) for p in ("x", "y")}, "half", "vm", "ALL_MAIN",
            {q: {"K1": v} for q, v in originals.items()}, same_state_declared=True)
        request = {"measurement": asdict(baseline), "target": asdict(Point("QL08", "exit")),
                   "magnets": {q: {"lower": -20, "upper": 20, "max_change": 1} for q in names}}
        w.result = MatchingResult(request, {"fingerprint": w.model.fingerprint}, asdict(Point("QL07")),
            {}, {}, {}, {}, {q: {"current": v, "suggested": v + .1, "change": .1}
                              for q, v in originals.items()}, "model_target_met", {})
        pvs = {resolve_write_target(w.context, q, quantity="K1").pv_name: v for q, v in originals.items()}
        before = dict(pvs)
        def write(pv, value, **kwargs):
            pvs[pv] = value
            return 1
        def synchronous(operation, completed):
            completed(operation(lambda: False))
        with tempfile.TemporaryDirectory() as directory:
            w.execution_runs = Path(directory) / "runs"
            w.execution_latest = Path(directory) / "matching_execution_latest.json"
            w.stale = False
            w.update_execution_buttons()
            self.assertTrue(w.apply_button.isEnabled())
            with patch("epics.caget", side_effect=lambda pv, **kw: pvs[pv]), \
                 patch("epics.caput", side_effect=write), patch.object(w, "run_task", side_effect=synchronous), \
                 patch.object(w, "error") as error:
                w.apply_suggestion()
                error.assert_not_called()
                self.assertEqual(w.execution["status"], "applied")
                self.assertTrue(w.execution_latest.exists())
                self.assertTrue(w.restore_button.isEnabled())
                self.assertFalse(w.apply_button.isEnabled())
                with patch("half_linac.src.apps.emit_measure.matching_workspace.resolve_app_runtime_paths",
                           return_value={"runs_dir": w.execution_runs}):
                    reopened = MatchingWorkspace(self.context)
                    self.assertTrue(reopened.restore_button.isEnabled())
                    self.assertEqual(reopened.execution["original"], originals)
                    reopened.timer.stop()
                    reopened.close()
                w.restore_previous()
                error.assert_not_called()
                self.assertEqual(pvs, before)
                self.assertEqual(w.execution["status"], "restored")
                self.assertFalse(w.restore_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
