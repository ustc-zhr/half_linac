from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-bba-batch-tests")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT.parent), str(ROOT / "src/apps/bba")]

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QApplication

from half_linac.src.apps.bba.batch import BBABatchDialog, BBABatchThread, save_preset_scans
from half_linac.src.apps.bba.main import BBAScanThread, ScanParameters, myWindow
from half_linac.src.apps.bba.fit_quality import fit_bba1_center
from half_linac.src.shared.machine_profile import load_app_context
from half_linac.src.shared.machine_profile.loader import load_bba_workflow


class BbaBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.context = load_app_context("bba", machine_id="half", control_backend="vm")

    def test_nested_overrides_keep_sampling_and_backend_defaults(self):
        profile = self.context.profile
        workflow = deepcopy(profile.workflows["bba"])
        preset = workflow["presets"][0]
        preset["scan"] = {
            "corrector": {"real": {"low": -0.7, "mode": "absolute"}},
            "sampling": {"settle_time_s": 12},
        }
        modified = replace(profile, workflows={**profile.workflows, "bba": workflow})
        real_scan = load_bba_workflow(modified, "real").presets[0].scan
        vm_scan = load_bba_workflow(modified, "vm").presets[0].scan
        self.assertEqual((real_scan.corr_from, real_scan.corr_end, real_scan.corr_mode), (-0.7, 1, "absolute"))
        defaults = profile.workflows["bba"]["bba1"]["scan"]["sampling"]
        self.assertEqual((real_scan.samples, real_scan.settle_time, real_scan.sample_interval), (defaults["samples_per_point"], 12, defaults["sample_interval_s"]))
        self.assertEqual((vm_scan.corr_from, vm_scan.corr_unit), (-0.001, "rad"))
        self.assertEqual(profile.workflows["bba"]["bba1"]["scan"]["sampling"], defaults)

    def test_save_scans_preserves_other_backend_and_unedited_data(self):
        profile = self.context.profile
        workflow = deepcopy(profile.workflows["bba"])
        real_before = load_bba_workflow(profile, "real").presets[0]
        vm_before = load_bba_workflow(profile, "vm").presets[0]
        edited = replace(real_before, scan=replace(real_before.scan, quad_from=-2, corr_end=0.6, quad_mode="absolute", settle_time=12))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bba.json"
            text = json.dumps(workflow, indent=2)
            path.write_text(text)
            saved = save_preset_scans(path, text, [real_before], [edited], "real", profile)
            updated = replace(profile, workflows={**profile.workflows, "bba": json.loads(saved)})
            real_after = load_bba_workflow(updated, "real").presets[0]
            vm_after = load_bba_workflow(updated, "vm").presets[0]
            self.assertEqual(real_after.scan, edited.scan)
            self.assertEqual(vm_after.scan, replace(vm_before.scan, settle_time=12))
            self.assertEqual(json.loads(saved)["presets"][1:], workflow["presets"][1:])
            self.assertEqual(json.loads(saved)["bba1"], workflow["bba1"])
            with self.assertRaisesRegex(ValueError, "changed on disk"):
                save_preset_scans(path, text, [real_before], [edited], "real", profile)

    def test_editor_applies_valid_values_and_blocks_invalid_start(self):
        window = myWindow()
        dialog = BBABatchDialog(window, BBAScanThread)
        try:
            original = dialog.presets[0]
            dialog.fields["corr_from"].setText("-0.75")
            dialog.fields["settle_time"].setText("12")
            self.assertTrue(dialog._commit_editor())
            self.assertEqual(dialog.presets[0].scan.corr_from, -0.75)
            self.assertEqual(window.get_setting(dialog.presets[0]).settle_time, 12)
            self.assertNotEqual(original.scan, dialog.presets[0].scan)
            dialog._select("x")
            dialog._apply_sampling()
            self.assertTrue(all(dialog.presets[row].scan.settle_time == 12 for row in dialog._rows()))
            dialog.fields["corr_steps"].setText("1")
            with patch.object(window, "_require_write_allowed") as write_check:
                dialog._start()
            write_check.assert_not_called()
            self.assertIsNone(dialog.worker)
            dialog.fields["corr_steps"].setText("5")
            dialog._commit_editor()
            dialog.originals = list(dialog.presets)
        finally:
            dialog.close()
            window.close()

    def _queue(self, root, behavior="success"):
        events = []
        tasks = [ScanParameters(preset_id=name, app_context=self.context, control_backend="vm", archive_dir=root / name)
                 for name in ("first", "second", "third")]

        class FakeScan(QThread):
            trigger = pyqtSignal(dict)

            def __init__(self, params):
                super().__init__()
                self.params = params
                self.stopped = False
                self.outcome = {}

            def preflight(self):
                events.append(("check", self.params.preset_id))
                if behavior == "preflight" and self.params.preset_id == "second":
                    raise RuntimeError("disconnected")

            def stop(self):
                self.stopped = True

            def run(self):
                events.append(("scan", self.params.preset_id))
                status = "success"
                if self.params.preset_id == "first":
                    if behavior == "failed":
                        status = "failed"
                    elif behavior == "stop":
                        queue.stop()
                        status = "stopped"
                self.outcome = {"status": status, "restored": True, "error": "", "offset_m": 0.0003}
                if behavior == "invalid_fit":
                    self.outcome["fit_quality"] = {"quality": "invalid"}
                events.append(("restore", self.params.preset_id))

        queue = BBABatchThread(tasks, FakeScan, root)
        return queue, events

    def test_queue_preflights_all_then_restores_before_next(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, events = self._queue(Path(directory))
            queue.run()
            self.assertEqual(queue.status, "success")
            self.assertEqual(events, [("check", name) for name in ("first", "second", "third")] +
                             [(operation, name) for name in ("first", "second", "third") for operation in ("scan", "restore")])
            saved = json.loads((Path(directory) / "batch.json").read_text())
            self.assertEqual(len(saved["items"]), 3)
            self.assertTrue(all(item["status"] == "success" for item in saved["items"]))

    def test_failed_stopped_and_failed_preflight_do_not_continue(self):
        for behavior, status, scans in (("failed", "failed", 1), ("stop", "stopped", 1), ("preflight", "failed", 0)):
            with self.subTest(behavior=behavior), tempfile.TemporaryDirectory() as directory:
                queue, events = self._queue(Path(directory), behavior)
                queue.run()
                self.assertEqual(queue.status, status)
                self.assertEqual(sum(operation == "scan" for operation, _ in events), scans)
                self.assertEqual(queue.records[-1]["status"], "not_run")

    def test_invalid_fit_does_not_stop_restored_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, events = self._queue(Path(directory), "invalid_fit")
            queue.run()
            self.assertEqual(queue.status, "success")
            self.assertEqual(sum(operation == "scan" for operation, _ in events), 3)
            saved = json.loads((Path(directory) / "batch.json").read_text())
            self.assertEqual(saved["items"][0]["fit_quality"]["quality"], "invalid")

    def test_quality_table_review_selection_and_viewer(self):
        window = myWindow()
        dialog = BBABatchDialog(window, BBAScanThread)
        try:
            positions = np.linspace(-0.001, 0.001, 5)
            quality = fit_bba1_center(positions, positions - 0.003)
            record = dict(status="success", offset_m=quality["offset_m"], fit_quality=quality, archive="/tmp/bba-review-test")
            dialog.selected_rows = [0]
            dialog._progress(0, record)
            self.assertEqual(dialog.table.item(0, 8).text(), "Review")
            self.assertIn("±", dialog.table.item(0, 6).text())
            dialog._select_review()
            self.assertEqual(dialog._rows(), [0])
            with patch.object(dialog, "_show_fit") as show:
                dialog._view_result(0)
            show.assert_called_once()
        finally:
            dialog.close()
            window.close()

    def test_scan_persists_final_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            params = ScanParameters(bba1_quad_scan_path=root / "bba1_quad_scan.txt", bba1_metadata_path=root / "metadata.json")
            scan = BBAScanThread(params)
            positions = np.linspace(-0.001, 0.001, 5)
            with patch.object(scan, "_perform_scan", return_value=(positions, positions - 0.0002)):
                scan.run()
            saved = json.loads(params.bba1_metadata_path.read_text())
            self.assertEqual(saved["outcome"]["fit_quality"]["quality"], "good")
            self.assertAlmostEqual(saved["outcome"]["offset_m"], 0.0002)

    def test_restore_attempts_both_magnets_after_first_write_fails(self):
        scan = BBAScanThread(ScanParameters())
        writes = []

        def write(pv, value):
            writes.append(pv)
            if pv == "quad":
                raise RuntimeError("quad disconnected")

        with patch.object(scan, "_safe_put", side_effect=write), patch.object(scan, "_safe_get", return_value=2):
            with self.assertRaisesRegex(RuntimeError, "quad disconnected"):
                scan._restore((("quad", 1), ("corr", 2)))
        self.assertEqual(writes, ["quad", "corr"])
        self.assertFalse(scan.outcome["restored"])

    def test_stop_saves_partial_points_and_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            params = ScanParameters(
                corrPV="corr", quadPV="quad", bpm1PV="bpm1", bpm2PV="bpm2",
                samples=1, quad_steps=3, corr_steps=2,
                bba1_data_path=root / "m1S.txt", bba1_quad_scan_path=root / "bba1_quad_scan.txt",
                bba1_metadata_path=root / "metadata.json",
            )
            scan = BBAScanThread(params)
            state = {"quad": 2., "corr": 0.}

            def emit(payload):
                if payload.get("show") == "scan_point":
                    scan.stop()

            with patch("half_linac.src.apps.bba.main.epics.PV", side_effect=lambda name: name), \
                 patch.object(scan, "_scan_ranges", return_value=(np.array([3., 4., 5.]), np.array([0., 1.]))), \
                 patch.object(scan, "_safe_get", side_effect=lambda pv, label: state[pv]), \
                 patch.object(scan, "_safe_put", side_effect=lambda pv, value: state.update({pv: value})), \
                 patch.object(scan, "_read_bpm_m", return_value=0.001), \
                 patch.object(scan, "_emit", side_effect=emit):
                scan.run()
            self.assertEqual(scan.outcome["status"], "stopped")
            self.assertTrue(scan.outcome["restored"])
            self.assertEqual(state, {"quad": 2., "corr": 0.})
            self.assertEqual(np.loadtxt(params.bba1_quad_scan_path, ndmin=2).shape, (1, 4))
            self.assertEqual(json.loads(params.bba1_metadata_path.read_text())["outcome"]["status"], "stopped")

    def test_restore_readback_timeout_is_a_failure(self):
        scan = BBAScanThread(ScanParameters())
        scan.restore_readbacks = [(SimpleNamespace(pvname="current_readback"), 2.)]
        with patch.object(scan, "_safe_put"), \
             patch.object(scan, "_safe_get", side_effect=[1., 3.]), \
             patch("half_linac.src.apps.bba.main.time.monotonic", side_effect=[0, 0, 0, 16]):
            with self.assertRaisesRegex(RuntimeError, "Current readback"):
                scan._restore((("quad", 1.),))
        self.assertFalse(scan.outcome["restored"])

    def test_batch_settings_ignore_manual_ranges_and_dialog_runs(self):
        with patch.dict(os.environ, {"HALFLINAC_MACHINE": "half", "HALFLINAC_CONTROL_BACKEND": "vm"}):
            window = myWindow()
        try:
            preset = window._bba_presets_for_family("bba1")[0]
            window.lineEdit.setText("-0.002")
            self.assertEqual(window.get_setting().corr_from, -0.002)
            window.lineEdit.setText("invalid manual input")
            self.assertEqual(window.get_setting(preset).corr_from, preset.scan.corr_from)
            with tempfile.TemporaryDirectory() as directory:
                queue, _ = self._queue(Path(directory))
                dialog = BBABatchDialog(window, queue.scan_factory)
                dialog._select("x")
                self.assertEqual(len(dialog._rows()), 40)
                dialog._select("none")
                dialog.table.item(0, 0).setCheckState(2)
                with patch.object(window, "_bba_runtime_paths", return_value={"runs_dir": Path(directory)}):
                    dialog._start()
                deadline = time.monotonic() + 5
                while window.scan is not None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.001)
                self.assertIsNone(window.scan)
                self.assertEqual(dialog.worker.status, "success")
                self.assertEqual(dialog.table.item(0, 5).text(), "success")
                self.assertTrue(window.tabWidget.isEnabled())
                dialog.close()
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
