"""Offline GUI checks for Global Correctors matrix selection."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT.parent, ROOT / "src/apps/orbit_correct"):
    sys.path.insert(0, str(path))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest
from half_linac.src.apps.orbit_correct.mainOrbCor import myWindow
from half_linac.src.apps.orbit_correct import profile_runtime as runtime


class MatrixSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for patcher in (
            patch.object(runtime, "ORBIT_RUNTIME_ROOT", Path(temp.name)),
            patch.dict(os.environ, {"HALF_LINAC_MACHINE_ID": "irfel",
                                    "HALF_LINAC_CONTROL_BACKEND": "vm"}),
            patch("half_linac.src.shared.process_runtime.ManagedProcessGroup.install_signal_handlers"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = myWindow()
        self.window.status_timer.stop()
        self.addCleanup(self.window.close)

    def save(self, bpms):
        return runtime.write_response_matrix_snapshot(
            self.window.app_context, np.eye(2 * len(bpms)), selected_bpms=bpms)

    def test_select_matrix_devices_preserves_values_and_method(self):
        w = self.window
        self.assertFalse(w.selectMatrixDevicesButton.isEnabled())
        record = self.save(["BPM07", "BPM10"])
        w.refresh_response_matrices()
        # Loading alone never expands or replaces the BPM selection.
        self.assertEqual(len(w.target_BPMs()[0]), 5)
        w.comboBox.setCurrentText("one-to-one")
        w.localResponseSourceComboBox.setCurrentIndex(
            w.localResponseSourceComboBox.findData("active_matrix"))
        w._bpmx_spinboxes[1].setValue(0.123)
        w._bpmy_spinboxes[4].setValue(-0.456)
        before = [(x.value(), y.value()) for x, y in zip(w._bpmx_spinboxes, w._bpmy_spinboxes)]
        w._set_global_correctors_checked(False)
        w.selectMatrixDevicesButton.click()
        self.assertEqual(w.target_BPMs()[0], ["BPM07", "BPM10"])
        self.assertEqual(w._selected_global_correctors(), (["HC02", "HC07"], ["VC02", "VC07"]))
        self.assertEqual(before, [(x.value(), y.value()) for x, y in zip(w._bpmx_spinboxes, w._bpmy_spinboxes)])
        self.assertEqual(w._selected_correction_method(), "one-to-one")
        self.assertEqual(w._selected_local_response_source(), "active_matrix")
        self.assertEqual(w.matrixCoverageLabel.text(), "Covers current selection.")
        w.cancelall()
        for process in ("response_matrix", "orbit_correction", "cor_off", "cor_recover"):
            with patch.object(w.process_manager, "is_running", side_effect=lambda key: key == process):
                w._refresh_status()
                self.assertFalse(w.selectMatrixDevicesButton.isEnabled())
                w._select_matrix_devices()
                self.assertEqual(w.target_BPMs()[0], [])
        w._refresh_status()
        self.assertTrue(w.selectMatrixDevicesButton.isEnabled())
        Path(record["matrix_path"]).write_text("invalid matrix")
        w.refresh_response_matrices()
        self.assertFalse(w.selectMatrixDevicesButton.isEnabled())

    def test_matrix_navigation_activation_and_live_coverage(self):
        w = self.window
        w.comboBox.setCurrentText("global")
        self.assertEqual(w.matrixCoverageLabel.text(), "No matrix selected.")
        first = self.save(["BPM07"])
        second = self.save(["BPM10"])
        w.refresh_response_matrices()
        self.assertIn("Missing:", w.matrixCoverageLabel.text())
        w.cancelall()
        w.all_checkboxes[4].setChecked(True)
        self.assertEqual(w.matrixCoverageLabel.text(), "Covers current selection.")
        self.assertNotIn(".txt", w.activeMatrixValueLabel.text())
        self.assertIn(".txt", w.activeMatrixValueLabel.toolTip())
        w._manage_response_matrices()
        self.assertIs(w.tabWidget.currentWidget(), w.tab_2)
        self.assertEqual(w.response_matrix_combo.currentData(), second["metadata_path"])
        w.response_matrix_combo.setCurrentIndex(w.response_matrix_combo.findData(first["metadata_path"]))
        self.assertEqual(runtime.get_active_response_matrix_record(w.app_context)["metadata_path"],
                         second["metadata_path"])
        w.load_response_matrix()
        self.assertIs(w.tabWidget.currentWidget(), w.tab_2)
        self.assertEqual(w._selected_global_correctors(), (["HC02"], ["VC02"]))
        self.assertIn("BPM10", w.matrixCoverageLabel.text())
        self.assertTrue(w.response_matrix_combo.currentText().startswith("[Current]"))
        w.returnToCorrectionButton.click()
        self.assertIs(w.tabWidget.currentWidget(), w.tab)
        w.comboBox.setCurrentText("one-to-one")
        self.assertTrue(w.matrixInfoGroup.isHidden())
        w.localResponseSourceComboBox.setCurrentIndex(
            w.localResponseSourceComboBox.findData("active_matrix"))
        self.assertFalse(w.matrixInfoGroup.isHidden())
        self.assertIn("BPM10", w.matrixCoverageLabel.text())
        w.cancelall()
        w.all_checkboxes[1].setChecked(True)
        self.assertEqual(w.matrixCoverageLabel.text(), "Covers current selection.")

    def test_target_wheel_scrolls_list_without_changing_values(self):
        w = self.window
        w.show()
        # Ensure even the short IRFEL list has a scrollable viewport.
        w.scrollArea.setFixedHeight(90)
        self.app.processEvents()
        bar = w.scrollArea.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        for spin in (w._bpmx_spinboxes[0], w._bpmy_spinboxes[0]):
            for focused in (False, True):
                for receiver in (spin, spin.lineEdit()):
                    with self.subTest(plane=spin.objectName(), focused=focused,
                                      receiver=type(receiver).__name__):
                        bar.setValue(0)
                        spin.setValue(0.123)
                        if focused:
                            spin.setFocus()
                        else:
                            w.clearBPMsButton.setFocus()
                        self.app.processEvents()
                        event = QWheelEvent(QPointF(5, 5),
                                            QPointF(receiver.mapToGlobal(QPoint(5, 5))),
                                            QPoint(), QPoint(0, -120), Qt.NoButton,
                                            Qt.NoModifier, Qt.NoScrollPhase, False)
                        QApplication.sendEvent(receiver, event)
                        self.assertAlmostEqual(spin.value(), 0.123)
                        self.assertGreater(bar.value(), 0)
            bar.setValue(0)
            spin.setFocus()
            QTest.keyClick(spin, Qt.Key_Up)
            self.assertAlmostEqual(spin.value(), 0.124)
            spin.lineEdit().selectAll()
            QTest.keyClicks(spin.lineEdit(), "0.456")
            QTest.keyClick(spin, Qt.Key_Return)
            self.assertAlmostEqual(spin.value(), 0.456)

    def test_measurement_completion_manual_subset_and_explicit_reload(self):
        w = self.window
        self.save(["BPM07", "BPM10"])
        w._response_scan_was_running = True
        w._refresh_status()  # Same completion path as the response measurement process.
        self.assertEqual(w._selected_global_correctors(), (["HC02", "HC07"], ["VC02", "VC07"]))
        w.global_xcor_checkboxes[1].setChecked(False)
        w.global_ycor_checkboxes[1].setChecked(False)
        w.refresh_response_matrices()
        w._refresh_status()
        self.assertEqual(w._selected_global_correctors(), (["HC07"], ["VC07"]))
        w.load_response_matrix()  # Explicit reload restores all covered correctors.
        self.assertEqual(w._selected_global_correctors(), (["HC02", "HC07"], ["VC02", "VC07"]))
        self.save(list(w.orbit_workflow.bpms))
        w.refresh_response_matrices()
        self.assertEqual(w._selected_global_correctors(),
                         (list(w.orbit_workflow.xcors), list(w.orbit_workflow.ycors)))

    def test_shared_bpm_panel_visibility_values_and_running_lock(self):
        w = self.window
        w.show()
        w.comboBox.setCurrentText("one-to-one")
        w._bpmx_spinboxes[1].setValue(0.123)
        w.all_checkboxes[0].setChecked(False)
        targets = w.target_BPMs()
        args = w._correction_parameter_args()
        self.assertFalse(w.tab.isAncestorOf(w.right_panel))
        self.assertFalse(w.tab_2.isAncestorOf(w.right_panel))
        w.tabWidget.setCurrentWidget(w.tab_2)
        self.app.processEvents()
        self.assertTrue(w.right_panel.isVisible())
        self.assertTrue(w.all_checkboxes[1].isVisible())
        self.assertFalse(w._bpmx_spinboxes[1].isVisible())
        self.assertFalse(w.saveTargetBPMsButton.isVisible())
        self.assertTrue(w.clearBPMsButton.isVisible())
        for process in ("response_matrix", "orbit_correction"):
            with patch.object(w.process_manager, "is_running", side_effect=lambda key: key == process):
                w._refresh_status()
                self.assertFalse(w.all_checkboxes[1].isEnabled())
                self.assertFalse(w.clearBPMsButton.isEnabled())
        w._refresh_status()
        self.assertTrue(w.all_checkboxes[1].isEnabled())
        w.tabWidget.setCurrentWidget(w.tab)
        self.app.processEvents()
        self.assertTrue(w.right_panel.isVisible())
        self.assertTrue(w._bpmx_spinboxes[1].isVisible())
        self.assertTrue(w.saveTargetBPMsButton.isVisible())
        self.assertEqual(w.target_BPMs(), targets)
        self.assertEqual(w._correction_parameter_args(), args)

    def test_one_to_one_arguments_and_targets_are_unchanged(self):
        w = self.window
        w.comboBox.setCurrentText("one-to-one")
        w._bpmx_spinboxes[1].setValue(0.123)
        w.all_checkboxes[0].setChecked(False)
        before_targets = w.target_BPMs()
        before_args = w._correction_parameter_args()
        before_source = w._selected_local_response_source()
        record = self.save(["BPM07"])
        w.refresh_response_matrices()
        w.load_response_matrix()
        self.assertEqual(w.target_BPMs(), before_targets)
        self.assertEqual(w._correction_parameter_args(), before_args)
        self.assertEqual(w._selected_local_response_source(), before_source)
        self.assertEqual(w._selected_correction_method(), "one-to-one")
        self.assertEqual(w._selected_global_correctors(), (["HC02"], ["VC02"]))
        # Matrix-backed one-to-one still requires its own BPM coverage.
        w.localResponseSourceComboBox.setCurrentIndex(
            w.localResponseSourceComboBox.findData("active_matrix"))
        with self.assertRaisesRegex(ValueError, "does not cover"):
            w._check_correction_matrix_coverage()


if __name__ == "__main__":
    unittest.main()
