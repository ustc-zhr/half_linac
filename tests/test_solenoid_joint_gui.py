import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
try:
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.solenoid_centering.joint_gui import JointCenteringDialog
    from half_linac.src.shared.machine_profile import load_app_context
except ImportError:
    QApplication = None


@unittest.skipUnless(QApplication is not None and os.environ.get('QT_QPA_PLATFORM') == 'offscreen',
                     'requires Qt offscreen')
class JointGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        context = load_app_context('solenoid_centering', machine_id='half', control_backend='real')
        self.dialog = JointCenteringDialog(context)
        self.addCleanup(self.dialog.close)

    def test_default_and_full_group_plan(self):
        self.assertEqual(len(self.dialog._plan().targets), 3)
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.dialog.group.setCurrentIndex(1)
        self.assertEqual(len(self.dialog._plan().targets), 5)
        self.assertEqual(self.dialog.targets.rowCount(), 5)

    def test_edit_invalidates_ready_and_old_result(self):
        self.dialog.ready = True
        self.dialog.result = {'recommendation_available': True}
        self.dialog._refresh()
        self.assertTrue(self.dialog.apply_button.isEnabled())
        self.dialog.probe.setValue(0.3)
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertFalse(self.dialog.apply_button.isEnabled())

    def test_applied_state_keeps_restore_available_and_locks_inputs(self):
        self.dialog.result = {'recommendation_available': True, 'applied': True}
        self.dialog._refresh()
        self.assertTrue(self.dialog.restore_button.isEnabled())
        self.assertFalse(self.dialog.group.isEnabled())
        self.assertFalse(self.dialog.apply_button.isEnabled())

    def test_preflight_failure_exposes_reason_without_modal_or_writes_claim(self):
        self.dialog.operation = 'preflight'
        with patch('half_linac.src.apps.solenoid_centering.joint_gui.QMessageBox.critical') as modal:
            self.dialog._failed('Failed to read PV: example:current:ao')
        modal.assert_not_called()
        self.assertIn('no settings changed', self.dialog.status.text())
        self.assertIn('example:current:ao', self.dialog.status.text())
        self.assertEqual(self.dialog.state_label.text(), 'CHECK FAILED')
        self.assertTrue(self.dialog.log_button.isChecked())
        self.assertEqual(self.dialog.log.objectName(), 'logView')

    def test_results_separate_current_and_response_units(self):
        self.dialog.scanner = SimpleNamespace(presets=[SimpleNamespace(solenoid='S1')])
        result = {'recommendation_available': True, 'relative_improvement': 0.5,
                  'original': {'C1': 0.1}, 'recommended': {'C1': 0.2},
                  'baseline_scores_mm': [0.04], 'final_scores_mm': [0.02],
                  'archive_path': '/tmp/synthetic-joint-result.json'}
        self.dialog._scan_done(result)
        self.assertEqual(self.dialog.results.item(0, 0).text(), 'C1')
        self.assertEqual(self.dialog.results.item(0, 3).text(), '+0.1000')
        self.assertEqual(self.dialog.responses.item(0, 0).text(), 'S1')
        self.assertEqual(self.dialog.responses.item(0, 3).text(), '+50.0%')
        self.assertEqual(self.dialog.state_label.text(), 'VALIDATED')


if __name__ == '__main__':
    unittest.main()
