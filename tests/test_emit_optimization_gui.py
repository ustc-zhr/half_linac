"""Offscreen GUI/scan bridge tests; EPICS is always mocked."""
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch, Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLCONFIGDIR', '/tmp/emit-optimization-test-mpl')
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/apps/emit_measure'))
from PyQt5.QtCore import QPoint, Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
from half_linac.src.apps.emit_measure import main
from half_linac.src.apps.emit_measure.optimization_gui import (
    OptimizationDialog, OptimizationWorker, OptimizationRunReviewDialog,
)
from half_linac.src.apps.emit_measure.optimization import OptimizationConfig, OptimizationSession
from half_linac.src.shared.machine_profile import load_app_context
from test_emit_optimization import Device, result, single_config


class FakeScan(QThread):
    trigger = pyqtSignal(dict)
    def __init__(self, paras):
        super().__init__()
        self.paras = paras
        self.stopped = False
        self.terminal_result = {'restored': False}
    def run(self):
        # Early fit must NOT release the request.
        self.trigger.emit({'method': 'leastSquares', **result(1)})
        time.sleep(.03)
        self.terminal_result = result(1)
        if self.stopped:
            self.terminal_result['error'] = 'Stopped'
    def stop(self):
        self.stopped = True


class LiveDisplayScan(FakeScan):
    def run(self):
        self.trigger.emit({'method': None, 'k1': 0.25, 'sigx': 1.2, 'sigy': 1.4})
        self.trigger.emit({'method': 'leastSquares', **result(1)})
        self.terminal_result = result(1)


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        patch.object(main.epics, 'caget', return_value=None).start()
        patch.object(main.epics, 'caput', side_effect=AssertionError('Real write forbidden')).start()
        self.addCleanup(patch.stopall)
        with patch.dict(os.environ, HALF_LINAC_MACHINE_ID='half', HALF_LINAC_CONTROL_BACKEND='vm'):
            self.host = main.myWindow()
        self.host.beam_image_timer.stop()
        self.host._beam_image_auto_refresh_ready = False
        self.host._show_optimization()
        self.dialog = self.host.optimization_dialog
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.dialog.fault = False
        if self.dialog.busy():
            self.dialog.stop()
            self.pump(lambda: not self.dialog.busy())
        self.dialog.unlock_host()
        self.host.close()
        self.dialog.close()

    def pump(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(predicate(), str(self.dialog.session.summary if self.dialog.session else self.dialog.status.text()))

    def test_vm_panel_cannot_write_and_main_selection_unchanged(self):
        selected = self.host.comboBox_4.currentText()
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertEqual(self.dialog.selected_names(), [])
        self.assertEqual(self.dialog.variables.rowCount(), 5)
        self.assertTrue(all(not (self.dialog.variables.item(i, 0).flags() & Qt.ItemIsEnabled) for i in range(5)))
        self.dialog.close()
        self.assertEqual(self.host.comboBox_4.currentText(), selected)
        main.epics.caput.assert_not_called()

    def test_default_size_and_compact_command_buttons(self):
        self.assertGreaterEqual(self.dialog.width(), 1120)
        self.assertGreaterEqual(self.dialog.height(), 940)
        for button in (self.dialog.read_button, self.dialog.start_button,
                       self.dialog.stop_button, self.dialog.apply_button,
                       self.dialog.restore_button):
            self.assertEqual(button.height(), 26)
        self.assertEqual(self.dialog.advanced_button.height(), 26)

    def test_search_setup_widgets_do_not_overlap_at_supported_sizes(self):
        def dialog_rect(widget):
            return widget.geometry().translated(widget.parentWidget().mapTo(self.dialog, QPoint(0, 0)))

        for width, height in ((980, 900), (800, 720)):
            with self.subTest(size=(width, height)):
                self.dialog.resize(width, height)
                self.app.processEvents()
                table = dialog_rect(self.dialog.variables)
                read = dialog_rect(self.dialog.read_button)
                budget = dialog_rect(self.dialog.budget_note)
                variables = dialog_rect(self.dialog.variable_panel)
                options = dialog_rect(self.dialog.options_panel)
                algorithm = dialog_rect(self.dialog.algorithm_settings)
                note = dialog_rect(self.dialog.measurement_note)
                self.assertLessEqual(read.bottom(), table.top())
                self.assertFalse(read.intersects(budget))
                self.assertLessEqual(variables.right(), options.left())
                self.assertTrue(options.contains(algorithm))
                self.assertLessEqual(table.bottom(), dialog_rect(self.dialog.lower_offset).top())
                self.assertLessEqual(variables.bottom(), budget.top())
                self.assertLessEqual(algorithm.bottom(), note.top())
                self.assertGreaterEqual(self.dialog.variables.height(), 148)
                if height == 720:
                    self.assertGreater(self.dialog.scroll_area.verticalScrollBar().maximum(), 0)
                else:
                    self.assertEqual(self.dialog.scroll_area.verticalScrollBar().maximum(), 0)

        self.dialog.algorithm.setCurrentIndex(self.dialog.algorithm.findData('bo'))
        self.dialog.advanced_button.setChecked(True)
        self.app.processEvents()
        self.assertLessEqual(self.dialog.scroll_content.width(), self.dialog.scroll_area.viewport().width())
        self.assertLessEqual(dialog_rect(self.dialog.bo_settings).bottom(),
                             dialog_rect(self.dialog.advanced_button).top())
        self.assertLessEqual(dialog_rect(self.dialog.advanced_button).bottom(),
                             dialog_rect(self.dialog.bo_advanced).top())

    def test_host_controls_and_timers_restored_exactly(self):
        button = self.host.pushButton
        before = button.isEnabled()
        self.dialog.lock_host()
        self.assertFalse(button.isEnabled())
        self.assertTrue(self.host.tabWidget.isEnabled())
        self.assertTrue(self.dialog.settings.isEnabled())
        self.dialog.unlock_host()
        self.assertEqual(button.isEnabled(), before)

    def test_unlock_after_scan_replaces_table_items(self):
        table = self.host.scan_points_table
        self.host._append_scan_point(0.1, 1.2, 1.4)
        surviving_table = QTableWidget(1, 1, self.host)
        surviving_item = QTableWidgetItem('Keep')
        surviving_table.setItem(0, 0, surviving_item)
        original_flags = surviving_item.flags()
        original_triggers = table.editTriggers()
        timer = QTimer(self.host)
        timer.start(60000)
        button_enabled = self.host.pushButton.isEnabled()

        self.dialog.lock_host()
        self.assertFalse(timer.isActive())
        self.assertFalse(surviving_item.flags() & Qt.ItemIsEditable)
        self.host._clear_scan_points()
        self.host._append_scan_point(0.2, 1.3, 1.5)
        replacement_flags = table.item(0, 0).flags()
        self.dialog.unlock_host()

        self.assertEqual(table.item(0, 0).flags(), replacement_flags)
        self.assertEqual(surviving_item.flags(), original_flags)
        self.assertEqual(table.editTriggers(), original_triggers)
        self.assertFalse(table.signalsBlocked())
        self.assertFalse(surviving_table.signalsBlocked())
        self.assertEqual(self.host.pushButton.isEnabled(), button_enabled)
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), 60000)
        self.assertFalse(self.host._optimization_locked)
        for saved in (self.dialog.locked_items, self.dialog.locked_tables,
                      self.dialog.locked_widgets, self.dialog.stopped_timers):
            self.assertEqual(saved, [])
        self.dialog.unlock_host()
        main.epics.caput.assert_not_called()

    def test_real_variable_table_supports_independent_selected_bounds(self):
        ctx = load_app_context('emit_measure', machine_id='half', control_backend='real')
        self.dialog.close()
        self.host.app_context, self.host.machine_profile, self.host.machine_type = ctx, ctx.profile, 'real'
        self.dialog = OptimizationDialog(self.host)
        self.assertEqual(len(self.dialog.selected_names()), 1)
        self.assertNotIn('QL09', self.dialog.variable_rows)
        for name, row in self.dialog.variable_rows.items():
            self.dialog.variables.item(row, 0).setCheckState(
                Qt.Checked if name in ('SS01', 'SS02') else Qt.Unchecked)
        for name, bounds in (('SS01', ('1', '9')), ('SS02', ('2', '10'))):
            row = self.dialog.variable_rows[name]
            for column, value in zip((3, 4), bounds):
                self.dialog.variables.item(row, column).setText(value)
        variables = {v.element_id: (v.low, v.high) for v in self.dialog.selected_variables()}
        self.assertEqual(variables, {'SS01': (1., 9.), 'SS02': (2., 10.)})
        self.assertIn('2 selected', self.dialog.budget_note.text())
        self.assertIn('More variables', self.dialog.budget_note.text())
        self.dialog.display_initial({'SS01': 5., 'SS02': 6.})
        self.assertEqual(self.dialog.variables.item(self.dialog.variable_rows['SS02'], 2).text(), '6')
        main.epics.caput.assert_not_called()

    def test_invalid_start_does_not_lock_existing_measurement(self):
        with patch.object(self.dialog, 'error') as error:
            self.dialog.start()
        error.assert_called_once()
        self.assertFalse(getattr(self.host, '_optimization_locked', False))

    def test_relative_bounds_use_each_selected_current_and_validate_atomically(self):
        ctx = load_app_context('emit_measure', machine_id='half', control_backend='real')
        self.dialog.close()
        self.host.app_context, self.host.machine_profile, self.host.machine_type = ctx, ctx.profile, 'real'
        self.dialog = OptimizationDialog(self.host)
        for name, row in self.dialog.variable_rows.items():
            self.dialog.variables.item(row, 0).setCheckState(
                Qt.Checked if name in ('SS01', 'SS02') else Qt.Unchecked)
        self.dialog.lower_offset.setValue(1)
        self.dialog.upper_offset.setValue(2)
        self.dialog.display_initial({'SS01': 5.})
        with patch.object(self.dialog, 'error') as error:
            self.dialog.set_relative_bounds()
        error.assert_called_once()
        self.assertEqual(self.dialog.variables.item(self.dialog.variable_rows['SS01'], 3).text(), '')
        self.dialog.display_initial({'SS02': 7.})
        self.dialog.set_relative_bounds()
        self.assertEqual({v.element_id: (v.low, v.high) for v in self.dialog.selected_variables()},
                         {'SS01': (4., 7.), 'SS02': (6., 9.)})
        for name, row in self.dialog.variable_rows.items():
            if name not in ('SS01', 'SS02'):
                self.assertEqual(self.dialog.variables.item(row, 3).text(), '')
        self.dialog.upper_offset.setValue(1e6)
        with patch.object(self.dialog, 'error') as error:
            self.dialog.set_relative_bounds()
        error.assert_called_once()
        self.assertEqual({v.element_id: (v.low, v.high) for v in self.dialog.selected_variables()},
                         {'SS01': (4., 7.), 'SS02': (6., 9.)})
        self.dialog.lower_offset.setValue(0)
        self.dialog.upper_offset.setValue(0)
        with patch.object(self.dialog, 'error') as error:
            self.dialog.set_relative_bounds()
        error.assert_called_once()
        main.epics.caput.assert_not_called()

    def test_solenoid_settle_is_passed_to_session_independently(self):
        from half_linac.src.apps.emit_measure.optimization import OptimizationVariable
        self.assertEqual(self.dialog.solenoid_settle.value(), 2.)
        self.host.lineEdit_24.setText('8')
        self.dialog.solenoid_settle.setValue(6.5)
        self.dialog.algorithm.setCurrentIndex(self.dialog.algorithm.findData('bo'))
        self.dialog.bo_initial_samples.setValue(4)
        self.dialog.bo_exploration.setValue(.025)
        self.dialog.bo_seed.setValue(12)
        self.dialog.other_limit.setText('3')
        paras = Mock(scan_metadata={}, background_image=None)
        with patch.object(self.dialog, 'selected_variables', return_value=(OptimizationVariable('SS01', 1, 9),)), \
             patch.object(self.host, 'optimization_parameters', return_value=paras), \
             patch.object(self.dialog, 'launch'):
            self.dialog.start()
        self.assertEqual(self.dialog.session.config.settle_time, 6.5)
        self.assertEqual(self.dialog.session.config.algorithm, 'bo')
        self.assertEqual(self.dialog.session.config.bo_initial_samples, 4)
        self.assertEqual(self.dialog.session.config.bo_exploration, .025)
        self.assertEqual(self.dialog.session.config.bo_random_seed, 12)
        self.assertNotIn('charge_monitor', paras.__dict__)
        self.assertIn('Bayesian Optimization', self.dialog.budget_note.text())
        self.assertEqual(self.host.lineEdit_24.text(), '8')
        self.dialog.session.confirmed = True
        self.dialog.solenoid_settle.setValue(7.)
        self.assertFalse(self.dialog.session.confirmed)
        self.assertEqual(self.dialog.session.config.settle_time, 6.5)
        main.epics.caput.assert_not_called()

    def test_algorithm_settings_are_dynamic_and_bo_default_tracks_variables(self):
        self.assertFalse(self.dialog.rcds_settings.isHidden())
        self.assertTrue(self.dialog.bo_settings.isHidden())
        self.dialog.algorithm.setCurrentIndex(self.dialog.algorithm.findData('bo'))
        self.assertTrue(self.dialog.rcds_settings.isHidden())
        self.assertFalse(self.dialog.bo_settings.isHidden())
        self.assertEqual(self.dialog.bo_initial_samples.value(), 3)
        self.dialog.advanced_button.setChecked(True)
        self.assertFalse(self.dialog.bo_advanced.isHidden())
        self.dialog.bo_initial_samples.setValue(8)
        self.dialog.variables.item(0, 0).setCheckState(Qt.Unchecked)
        self.assertEqual(self.dialog.bo_initial_samples.value(), 8)
        self.dialog.count.setValue(7)
        self.assertIn('exceed the search budget', self.dialog.budget_note.text())
        self.dialog.algorithm.setCurrentIndex(self.dialog.algorithm.findData('rcds'))
        self.assertFalse(self.dialog.rcds_settings.isHidden())
        self.assertTrue(self.dialog.bo_settings.isHidden())

    def test_constrained_bo_settings_create_optional_ict01_monitor(self):
        from half_linac.src.apps.emit_measure.optimization import OptimizationVariable
        ctx = load_app_context('emit_measure', machine_id='half', control_backend='real')
        self.host.app_context, self.host.machine_profile, self.host.machine_type = ctx, ctx.profile, 'real'
        self.dialog.algorithm.setCurrentIndex(self.dialog.algorithm.findData('cbo'))
        self.dialog.charge_retention.setValue(93.)
        self.dialog.other_limit.setText('3')
        self.assertFalse(self.dialog.charge_settings.isHidden())
        self.assertFalse(self.dialog.bo_settings.isHidden())
        paras = Mock(scan_metadata={}, background_image=None)
        with patch.object(self.dialog, 'error') as error, \
             patch.object(self.dialog, 'selected_variables',
                          return_value=(OptimizationVariable('SS01', 1, 9),)), \
             patch.object(self.host, 'optimization_parameters', return_value=paras), \
             patch.object(self.dialog, 'launch'):
            self.dialog.start()
        self.assertFalse(error.called, error.call_args)
        config = self.dialog.session.config
        self.assertEqual(config.algorithm, 'cbo')
        self.assertAlmostEqual(config.charge_constraint.retention, .93)
        self.assertEqual(paras.charge_monitor['element_id'], 'ICT01')
        self.assertEqual(paras.charge_monitor['pv'], 'IN:BD:ICT1:C')
        self.assertEqual(paras.charge_monitor['unit'], 'nC')
        self.assertEqual(paras.charge_monitor['trim_fraction'], .1)
        main.epics.caput.assert_not_called()

    def test_charge_summary_uses_trimmed_mean_and_rejects_low_coverage(self):
        scan = main.scanThread.__new__(main.scanThread)
        QThread.__init__(scan)
        scan.charge_connection_error = None
        scan.charge_monitor = {
            'element_id': 'ICT01', 'channel': 'charge', 'unit': 'nC',
            'trim_fraction': .1, 'minimum_samples': 3, 'minimum_valid_fraction': .8,
        }
        scan.charge_attempts = 10
        scan.charge_samples = [
            {'status': 'valid', 'value': value}
            for value in (.1, 1, 1, 1, 1, 1, 1, 1, 1, 10)
        ]
        summary = scan._charge_summary()
        self.assertEqual(summary['status'], 'valid')
        self.assertEqual(summary['trimmed_each_side'], 1)
        self.assertAlmostEqual(summary['value'], 1.)
        scan.charge_attempts = 4
        scan.charge_samples = [{'status': 'valid', 'value': 1.}] * 3
        self.assertEqual(scan._charge_summary()['status'], 'invalid')

    def test_charge_sampler_rejects_stale_and_nonpositive_values(self):
        scan = main.scanThread.__new__(main.scanThread)
        QThread.__init__(scan)
        scan.charge_samples = []
        scan.charge_attempts = 0
        scan.charge_monitor = {
            'element_id': 'ICT01', 'channel': 'charge', 'unit': 'nC',
            'scale': 1., 'stale_timeout_s': 3.,
        }
        scan.charge_pv = Mock()
        for metadata, error_text in (({'value': 1., 'timestamp': 1.}, 'stale'),
                                     ({'value': 0., 'timestamp': 10.}, 'positive')):
            scan.charge_pv.get_with_metadata.return_value = metadata
            with patch('half_linac.src.apps.emit_measure.main.time.time', return_value=10.):
                sample = scan._read_charge_sample()
            self.assertEqual(sample['status'], 'invalid')
            self.assertIn(error_text, sample['error'])
        main.epics.caput.assert_not_called()

    def test_parameters_follow_main_selection_and_freeze_background(self):
        import numpy as np
        host = self.host
        ctx = load_app_context('emit_measure', machine_id='half', control_backend='real')
        host.app_context, host.machine_profile, host.machine_type = ctx, ctx.profile, 'real'
        for preset_id in ('emit_ql09_prf03', 'emit_qt02_prf07', 'emit_qt23_prf12'):
            with self.subTest(preset=preset_id):
                preset = host._find_emit_preset(preset_id)
                host._apply_emit_preset(preset)
                host.scan_strategy_combo.setCurrentIndex(host.scan_strategy_combo.findData('adaptive_quality'))
                host.lineEdit_2.setText('123.45')
                host.lineEdit_10.setText('4')
                geometry = host._current_flag_pixel_geometry(preset.flag)
                host.background_image = np.zeros(geometry.shape)
                quad = Mock(initial_k1=0., initial_current=5., initial_readback=5.)
                with patch.object(host, '_prepare_emit_model_snapshot') as snapshot, \
                     patch('half_linac.src.apps.emit_measure.optimization.VerifiedQuadRestore', return_value=quad) as restore, \
                     patch.object(host, '_background_reference_is_usable', return_value=True), \
                     patch.object(host.beam_image_background_checkbox, 'isChecked', return_value=True), \
                     patch.object(main.epics, 'caget', return_value=np.zeros(geometry.shape)):
                    paras = host.optimization_parameters()
                self.assertEqual((paras.quad_name, paras.flag_name), (preset.quad, preset.flag))
                self.assertEqual(paras.scan_strategy, 'adaptive_quality')
                self.assertEqual(paras.EnergyMeV, 123.45)
                self.assertEqual(paras.samples, 4)
                self.assertEqual(paras.model_line, preset.model_line)
                self.assertEqual(host.comboBox_4.currentText(), preset.flag)
                self.assertEqual(host.comboBox.currentText(), preset.quad)
                self.assertEqual(paras.scan_metadata['initial_quad']['element'], preset.quad)
                restore.assert_called_once_with(ctx, preset.quad)
                snapshot.assert_called_once_with(paras)
                host.background_image[:] = 1
                self.assertTrue(np.all(paras.background_image == 0))
                host.lineEdit_2.setText('200')
                self.assertEqual(paras.EnergyMeV, 123.45)
                self.dialog.refresh_note()
                self.assertIn(f'{preset.quad} → {preset.flag}', self.dialog.context_label.text())
                self.assertEqual(self.dialog.energy.text(), '200')
        main.epics.caput.assert_not_called()

    def test_grid_mode_is_kept_for_optimization(self):
        ctx = load_app_context('emit_measure', machine_id='half', control_backend='real')
        self.host.app_context, self.host.machine_profile, self.host.machine_type = ctx, ctx.profile, 'real'
        self.host.scan_strategy_combo.setCurrentIndex(self.host.scan_strategy_combo.findData('grid'))
        self.assertEqual(self.host._selected_scan_strategy(), 'grid')
        self.dialog.refresh_note()
        self.assertNotIn('Adaptive or Adaptive Quality required', self.dialog.measurement_note.text())
        main.epics.caput.assert_not_called()

    def test_non_solenoid_rejected_before_image_or_quad_reads(self):
        from half_linac.src.apps.emit_measure.optimization import OptimizationVariable
        host = self.host
        ctx = load_app_context('emit_measure', machine_id='half', control_backend='real')
        host.app_context, host.machine_profile, host.machine_type = ctx, ctx.profile, 'real'
        host._apply_emit_preset(host._find_emit_preset('emit_ql09_prf03'))
        main.epics.caget.reset_mock()
        with self.assertRaisesRegex(ValueError, 'select a solenoid'):
            host.optimization_parameters((OptimizationVariable('QL09', 1, 9),))
        main.epics.caget.assert_not_called()
        main.epics.caput.assert_not_called()

    def test_catalog_order_does_not_reject_ss01_for_ql13(self):
        import numpy as np
        from half_linac.src.apps.emit_measure.optimization import OptimizationVariable
        host = self.host
        ctx = load_app_context('emit_measure', machine_id='half', control_backend='real')
        host.app_context, host.machine_profile, host.machine_type = ctx, ctx.profile, 'real'
        host._apply_emit_preset(host._find_emit_preset('emit_ql09_prf03'))
        paras = host.get_setting(show_warning=False)
        paras.quad_name, paras.flag_name = 'QL13', 'PRF04'
        self.assertGreater(ctx.profile.get_element('SS01').order, ctx.profile.get_element('QL13').order)
        quad = Mock(initial_k1=0., initial_current=5., initial_readback=5.)
        with patch.object(host, 'get_setting', return_value=paras), \
             patch.object(host, '_prepare_emit_model_snapshot'), \
             patch('half_linac.src.apps.emit_measure.optimization.VerifiedQuadRestore', return_value=quad), \
             patch.object(main.epics, 'caget', return_value=np.zeros(paras.flag_pixel_shape)):
            frozen = host.optimization_parameters((OptimizationVariable('SS01', 1, 9),))
        self.assertEqual(frozen.quad_name, 'QL13')
        main.epics.caput.assert_not_called()

    def test_delayed_refresh_is_blocked_when_closed_or_optimizing(self):
        self.host._optimization_locked = True
        self.assertFalse(self.host.refresh_current_beam_image_fit())
        self.host._optimization_locked = False
        self.host._closing = True
        self.assertFalse(self.host.refresh_current_beam_image_fit())

    def test_request_waits_for_scan_exit_and_uses_private_archive(self):
        self.dialog.paras = main.structData()
        done = Event()
        request = {'path': Path('/tmp/emit-test-private'), 'done': done}
        with patch.object(main, 'scanThread', FakeScan):
            self.dialog.session = Mock()
            self.dialog.session.cancelled.is_set.return_value = False
            self.dialog.start_measurement(request)
            self.assertFalse(done.is_set())
            self.assertEqual(self.dialog.scan.paras.scan_latest_dir, request['path'] / 'latest')
            self.pump(done.is_set)
        self.assertTrue(request['result']['restored'])
        self.dialog.session = None

    def test_optimization_scan_updates_main_without_switching_to_analysis(self):
        self.dialog.paras = main.structData()
        self.dialog.session = Mock(records=[], cancelled=Mock())
        self.dialog.session.cancelled.is_set.return_value = False
        request = {'path': Path('/tmp/emit-test-live-display'), 'done': Event()}
        self.host.tabWidget.setCurrentWidget(self.host.X_Plane)
        with patch.object(main, 'scanThread', LiveDisplayScan):
            self.dialog.start_measurement(request)
            self.pump(request['done'].is_set)
            self.app.processEvents()
        self.assertEqual(self.host.scan_points_table.rowCount(), 1)
        self.assertTrue(self.host._scan_result_ready)
        self.assertIs(self.host.tabWidget.currentWidget(), self.host.X_Plane)
        self.assertIn('Optimization measurement complete', self.host.scan_strategy_status_label.text())
        main.epics.caput.assert_not_called()
        self.dialog.session = None

    def test_close_during_scan_waits_for_restore(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        device = Device()
        session = OptimizationSession(single_config('SS01', 1, 9, 'x', 3), device, None,
                                      Path(temp.name) / 'run', optimizer=lambda *args: None)
        self.dialog.session = session
        self.dialog.paras = main.structData()
        start = self.dialog.start_measurement
        closed = []
        def start_and_close(request):
            start(request)
            closed.append(self.dialog.close())
        with patch.object(main, 'scanThread', FakeScan), patch.object(self.dialog, 'start_measurement', start_and_close):
            self.dialog.launch()
            self.pump(lambda: not self.dialog.busy())
            self.assertEqual(closed, [False])
            self.assertEqual(device.restores, [5])
            self.assertEqual(session.summary['status'], 'stopped')

    def test_terminal_scan_restore_failure_overrides_fit_success(self):
        paras = self.host.get_setting()
        paras.recal = False
        scan = main.scanThread(paras)
        scan.recal = False
        scan.restore_quad_callback = Mock(side_effect=RuntimeError('QL09 stuck'))
        with patch.object(main.epics, 'caget', return_value=0), \
             patch.object(scan, '_run_grid_scan', side_effect=RuntimeError('stop before model')):
            scan.scan_strategy = 'grid'
            scan.run()
        self.assertFalse(scan.terminal_result['restored'])
        self.assertEqual(scan.terminal_result['restore_error'], 'QL09 stuck')
        main.epics.caput.assert_not_called()

    def test_complete_worker_bridge_preserves_ordinary_latest(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        ordinary = root / 'latest'
        ordinary.mkdir()
        sentinel = ordinary / 'scanResults.txt'
        sentinel.write_text('ordinary measurement')
        session = OptimizationSession(single_config('SS01', 1, 9, 'x', 3), Device(), None,
                                      root / 'run', optimizer=lambda fn, *_: fn(3))
        self.dialog.session = session
        self.dialog.paras = main.structData()
        with patch.object(main, 'scanThread', FakeScan):
            self.dialog.launch()
            self.pump(lambda: not self.dialog.busy())
        self.assertEqual(session.summary['status'], 'complete')
        self.assertEqual(len(session.records), 5)
        self.assertTrue(session.summary['restored'])
        self.assertEqual(sentinel.read_text(), 'ordinary measurement')

    def test_settings_change_invalidates_apply_without_losing_initial(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        session = OptimizationSession(single_config('SS01', 1, 9, 'x', 3), Device(), None,
                                      Path(temp.name) / 'run')
        session.confirmed, session.initial = True, (5,)
        self.dialog.session = session
        self.dialog.variables.item(0, 3).setText('2')
        self.assertFalse(session.confirmed)
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.assertTrue(self.dialog.restore_button.isEnabled())

    def test_completed_result_opens_read_only_diagnostics(self):
        record = {'index': 1, 'stage': 'Baseline', 'archive': '/tmp/measurement_001',
                  'finished_at': 1, 'valid': True, 'feasible': True,
                  'currents': {'SS01': 5.}, 'values': {'x': 1., 'y': 2.}}
        self.dialog._visible_records = [record]
        self.dialog.session = Mock(run_dir=Path('/tmp/run'))
        viewer = Mock()
        with patch('half_linac.src.apps.emit_measure.optimization_gui.MeasurementDiagnosticsDialog',
                   return_value=viewer) as diagnostics:
            self.dialog.open_current_diagnostics(0)
        diagnostics.assert_called_once_with(record, run_dir=Path('/tmp/run'), parent=self.dialog)
        viewer.exec_.assert_called_once_with()
        main.epics.caput.assert_not_called()

    def test_open_run_uses_separate_read_only_viewer(self):
        payload = {'schema_version': 'emit_optimization_v2', 'records': []}
        run_dir = Path('/tmp/optimization_archive')
        viewer = Mock()
        session_before = self.dialog.session
        with patch('half_linac.src.apps.emit_measure.optimization_gui.QFileDialog.getOpenFileName',
                   return_value=(str(run_dir / 'optimization.json'), '')), \
             patch('half_linac.src.apps.emit_measure.optimization_gui.load_optimization_run',
                   return_value=(payload, run_dir)), \
             patch('half_linac.src.apps.emit_measure.optimization_gui.OptimizationRunReviewDialog',
                   return_value=viewer):
            self.dialog.open_run_archive()
        self.assertIs(self.dialog.session, session_before)
        viewer.exec_.assert_called_once_with()
        main.epics.caput.assert_not_called()

    def test_archive_review_restores_both_emittance_curves(self):
        records = [
            {'index': 1, 'valid': True, 'feasible': True, 'values': {'x': 3., 'y': 2.}},
            {'index': 2, 'valid': False, 'values': {}},
            {'index': 3, 'valid': True, 'feasible': False, 'values': {'x': 1., 'y': 4.}},
            {'index': 4, 'stage': 'Candidate'},
        ]
        viewer = OptimizationRunReviewDialog({'records': records}, '/tmp/run', self.dialog)
        self.addCleanup(viewer.close)
        self.assertEqual(viewer.table.rowCount(), 4)
        self.assertEqual(len(viewer.axes.lines), 2)
        for line, label, values in zip(viewer.axes.lines, ('X', 'Y'), ([3., 1.], [2., 4.])):
            self.assertEqual(line.get_label(), label)
            self.assertEqual(list(line.get_xdata()), [1, 3])
            self.assertEqual(list(line.get_ydata()), values)
        viewer.canvas.draw()
        main.epics.caput.assert_not_called()

    def test_archive_review_without_valid_measurements(self):
        for records in ([], [{'index': 1, 'valid': False}]):
            with self.subTest(records=records):
                viewer = OptimizationRunReviewDialog({'records': records}, '/tmp/run', self.dialog)
                self.addCleanup(viewer.close)
                self.assertEqual(len(viewer.axes.lines), 0)
                self.assertIn('No valid measurements', viewer.axes.texts[0].get_text())
                viewer.canvas.draw()

    def test_archive_review_restores_charge_curve_and_limit(self):
        records = [
            {'index': 1, 'valid': True, 'feasible': True,
             'values': {'x': 3., 'y': 2.}, 'charge': 1.0},
            {'index': 2, 'valid': True, 'feasible': False,
             'constraint_failures': ['charge_limit'],
             'values': {'x': 2., 'y': 2.}, 'charge': .9},
        ]
        payload = {
            'records': records,
            'charge_constraint': {'minimum': .95, 'unit': 'nC'},
        }
        viewer = OptimizationRunReviewDialog(payload, '/tmp/run', self.dialog)
        self.addCleanup(viewer.close)
        self.assertEqual(viewer.table.columnCount(), 7)
        self.assertIsNotNone(viewer.charge_axes)
        self.assertEqual(len(viewer.charge_axes.lines), 2)
        self.assertEqual(list(viewer.charge_axes.lines[0].get_ydata()), [1., .9])
        viewer.canvas.draw()


if __name__ == '__main__':
    unittest.main()
