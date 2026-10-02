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
    from half_linac.src.apps.solenoid_centering.main import MainWindow
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

    def test_main_window_shows_joint_as_peer_mode_and_locks_other_mode(self):
        window = MainWindow()
        self.addCleanup(window.close)
        self.assertEqual([window.mode_tabs.tabText(i) for i in range(2)],
                         ['Single Solenoid', 'Joint Centering'])
        self.assertIs(window.mode_tabs.widget(1), window.joint_view)
        self.assertFalse(window.joint_view.isWindow())
        self.assertIs(window.joint_view.layout().itemAt(0).widget(), window.joint_view.splitter)
        window.show()
        self.app.processEvents()
        header = window.status_strip.parentWidget()
        self.assertGreater(window.mode_tabs.geometry().top(), header.geometry().bottom())
        single_readiness = window.status_strip.items['READINESS'].value_label.text()
        window.mode_tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertTrue(window.status_strip.isVisible())
        self.assertFalse(window.log_button.isVisible())
        self.assertEqual(window.status_strip.items['PRESET'].title_label.text(), 'Group')
        self.assertEqual(window.status_strip.items['READINESS'].title_label.text(), 'Readiness')
        self.assertEqual(window.status_strip.items['READBACK VERIFIED'].title_label.text(), 'Readback')
        self.assertEqual(window.status_strip.items['WORKFLOW'].value_label.text(), 'Not checked')
        self.assertTrue(all(not item.value_label.wordWrap()
                            for item in window.status_strip.items.values()))
        window.joint_view.group.setCurrentIndex(1)
        self.app.processEvents()
        group_label = window.status_strip.items['PRESET'].value_label
        self.assertGreaterEqual(group_label.width(), group_label.sizeHint().width())
        window.joint_view.probe.setValue(0.3)
        self.assertEqual(group_label.text(), 'Custom')
        window.joint_view.reset_button.click()
        self.assertEqual(group_label.text(), window.joint_view.group.currentText())
        window.joint_view.ready = True
        window.joint_view._state('READY', 'success')
        self.assertEqual(window.status_strip.items['READINESS'].value_label.text(), 'Ready')
        self.assertEqual(window.status_strip.items['READBACK VERIFIED'].value_label.text(), 'Verified')
        window.joint_view.ready = False
        window.joint_view.result = {
            'operation_status': 'completed', 'restore': 'verified',
            'recommendation_available': False, 'relative_improvement': -0.0117,
        }
        window.joint_view._state('NOT VALIDATED', 'warning')
        self.assertEqual(window.status_strip.items['RESULT QUALITY'].value_label.text(),
                         'No valid recommendation')
        self.assertEqual(window.status_strip.items['LAST RESULT'].value_label.text(),
                         '-1.2% response')
        window.resize(1120, 720)
        self.app.processEvents()
        action_bottom = window.joint_view.start_button.mapTo(
            window, window.joint_view.start_button.rect().bottomLeft()).y()
        self.assertLess(action_bottom, window.height())
        window.mode_tabs.setCurrentIndex(0)
        self.assertTrue(window.status_strip.isVisible())
        self.assertEqual(window.status_strip.items['PRESET'].title_label.text(), 'Preset')
        self.assertEqual(window.status_strip.items['READINESS'].value_label.text(), single_readiness)
        window._single_operation_active = True
        window._refresh_mode_access()
        self.assertFalse(window.mode_tabs.isTabEnabled(1))
        window._single_operation_active = False
        window.joint_view.worker = object()
        window.joint_view.busy_changed.emit(True)
        self.assertFalse(window.mode_tabs.isTabEnabled(0))
        window.joint_view.worker = None
        window.joint_view.busy_changed.emit(False)
        self.assertTrue(window.mode_tabs.isTabEnabled(0))
        self.assertTrue(window.mode_tabs.isTabEnabled(1))

    def test_compact_actions_and_inline_preset(self):
        self.dialog.show()
        self.app.processEvents()
        preset_y = self.dialog.preset_label.mapToGlobal(
            self.dialog.preset_label.rect().center()).y()
        combo_y = self.dialog.group.mapToGlobal(self.dialog.group.rect().center()).y()
        self.assertEqual(self.dialog.preset_label.text(), 'Preset')
        self.assertLess(abs(preset_y - combo_y), 15)
        self.assertLess(self.dialog.preflight_button.mapToGlobal(
            self.dialog.preflight_button.rect().center()).y(), combo_y)
        self.assertLessEqual(self.dialog.start_button.height(), 36)
        self.assertLessEqual(self.dialog.log_button.height(), 36)

    def test_default_and_full_group_plan(self):
        self.assertEqual(len(self.dialog._plan().targets), 3)
        self.assertEqual(self.dialog._plan().correctors,
                         ('XC02', 'YC02', 'SM01-DX', 'SM01-DY', 'SL01-DX', 'SL01-DY'))
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.dialog.group.setCurrentIndex(1)
        self.assertEqual(len(self.dialog._plan().targets), 5)
        self.assertEqual(self.dialog.targets.rowCount(), 5)

    def test_custom_targets_correctors_and_reset(self):
        self.dialog.ready = True
        self.dialog.result = {'recommendation_available': True}
        self.dialog._add_target()
        self.assertEqual(self.dialog.targets.rowCount(), 4)
        self.assertEqual(self.dialog.group_display_name(), 'Custom')
        self.assertFalse(self.dialog.ready)
        self.assertIsNone(self.dialog.result)
        row = self.dialog.targets.rowCount() - 1
        selector = self.dialog.targets.cellWidget(row, 0)
        self.assertEqual(selector.currentData(), self.dialog._plan().targets[-1].preset_id)
        bpm_button = self.dialog.targets.cellWidget(row, 1)
        bpm_actions = bpm_button.menu().actions()
        alternate = next(action for action in bpm_actions if not action.isChecked())
        alternate.setChecked(True)
        self.dialog.targets.cellWidget(row, 2).setValue(0.65)
        corrector_action = next(action for action in self.dialog.corrector_button.menu().actions()
                                if action.isChecked())
        corrector_action.setChecked(False)
        plan = self.dialog._plan()
        self.assertEqual(plan.id, 'custom')
        self.assertIn(alternate.text(), plan.targets[-1].bpms)
        self.assertEqual(plan.targets[-1].modulation_a, 0.65)
        self.assertNotIn(corrector_action.text(), plan.correctors)
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.dialog.targets.cellWidget(row, 3).click()
        self.assertEqual(self.dialog.targets.rowCount(), 3)
        self.dialog.reset_button.click()
        self.assertEqual(self.dialog._plan().id, self.dialog.plans[0].id)
        self.assertEqual(self.dialog._plan().targets, self.dialog.plans[0].targets)
        self.assertEqual(self.dialog.group_display_name(), self.dialog.plans[0].display_name)

    def test_invalid_custom_selection_fails_before_pv_access(self):
        first = self.dialog.targets.cellWidget(0, 0).currentData()
        second = self.dialog.targets.cellWidget(1, 0)
        second.setCurrentIndex(second.findData(first))
        self.assertEqual(self.dialog.group_display_name(), 'Custom')
        with patch.object(self.dialog, '_launch') as launch:
            self.dialog.preflight()
        launch.assert_not_called()
        self.assertEqual(self.dialog.state_label.text(), 'CHECK FAILED')
        self.assertIn('unique', self.dialog.status.text())
        self.dialog.reset_button.click()
        for action in self.dialog.corrector_button.menu().actions():
            action.setChecked(False)
        with patch.object(self.dialog, '_launch') as launch:
            self.dialog.preflight()
        launch.assert_not_called()
        self.assertIn('at least one corrector', self.dialog.status.text())

    def test_diagnostic_result_shows_sensitivity_without_apply(self):
        self.dialog.group.setCurrentIndex(2)
        result = {
            'mode': 'response_diagnostic', 'recommendation_available': False,
            'archive_path': '/tmp/ss01-response.json',
            'baseline_slopes_mm_per_a': {'BPM01': {'x': -2.0, 'y': 0.3}},
            'sensitivities': [{
                'corrector': 'XC00', 'bpm': 'BPM01', 'plane': 'x',
                'slope_minus_mm_per_a': -2.2,
                'slope_plus_mm_per_a': -1.8,
                'sensitivity_mm_per_a2': 1.0,
            }],
        }
        self.dialog._scan_done(result)
        self.assertEqual(self.dialog.state_label.text(), 'MEASURED')
        self.assertEqual(self.dialog.sensitivities.item(0, 6).text(), '+1.00000')
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.assertFalse(self.dialog.apply_button.isVisible())

    def test_edit_invalidates_ready_and_old_result(self):
        self.dialog.ready = True
        self.dialog.result = {'recommendation_available': True}
        self.dialog._refresh()
        self.assertTrue(self.dialog.apply_button.isEnabled())
        self.dialog.probe.setValue(0.3)
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertFalse(self.dialog.apply_button.isEnabled())

    def test_overview_tracks_custom_plan_and_preflight_estimate(self):
        self.assertEqual(self.dialog.overview_targets.text(), '3')
        self.assertEqual(self.dialog.overview_bpms.text(), '2')
        self.assertEqual(self.dialog.overview_correctors.text(), '6')
        self.dialog._add_target()
        self.assertEqual(self.dialog.overview_targets.text(), '4')
        self.dialog.scanner = SimpleNamespace(prepared_state=None)
        report = {'original': {}, 'correctors': ['XC02'], 'points_upper_bound': 24,
                  'estimated_minimum_seconds': 120, 'ranges_a': {}}
        self.dialog._preflight_done(report)
        self.assertEqual(self.dialog.overview_estimate.text(), '2.0 min')
        self.dialog.targets.cellWidget(3, 3).click()
        self.assertEqual(self.dialog.overview_targets.text(), '3')
        self.assertEqual(self.dialog.overview_estimate.text(), '—')
        self.assertFalse(self.dialog.ready)

    def test_sampling_settings_expand_without_changing_plan(self):
        original = self.dialog._plan()
        self.assertFalse(self.dialog.advanced_panel.isVisible())
        self.dialog.show()
        self.app.processEvents()
        self.dialog.advanced_button.click()
        self.assertTrue(self.dialog.advanced_panel.isVisible())
        self.assertEqual(self.dialog._plan(), original)
        self.dialog.advanced_button.click()
        self.assertFalse(self.dialog.advanced_panel.isVisible())
        self.assertIs(self.dialog.result_stack.currentWidget(), self.dialog.empty_results)

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
