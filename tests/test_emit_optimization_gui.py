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
from PyQt5.QtWidgets import QApplication
from half_linac.src.apps.emit_measure import main
from half_linac.src.apps.emit_measure.optimization_gui import OptimizationDialog, OptimizationWorker
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
                settings = dialog_rect(self.dialog.settings)
                algorithm = dialog_rect(self.dialog.algorithm_settings)
                note = dialog_rect(self.dialog.measurement_note)
                self.assertLessEqual(table.bottom(), read.top())
                self.assertFalse(read.intersects(budget))
                self.assertLessEqual(settings.bottom(), algorithm.top())
                self.assertLessEqual(algorithm.bottom(), note.top())
                self.assertGreaterEqual(self.dialog.variables.height(), 148)
                if height == 720:
                    self.assertGreater(self.dialog.scroll_area.verticalScrollBar().maximum(), 0)
                else:
                    self.assertEqual(self.dialog.scroll_area.verticalScrollBar().maximum(), 0)

    def test_host_controls_and_timers_restored_exactly(self):
        button = self.host.pushButton
        before = button.isEnabled()
        self.dialog.lock_host()
        self.assertFalse(button.isEnabled())
        self.assertTrue(self.host.tabWidget.isEnabled())
        self.assertTrue(self.dialog.settings.isEnabled())
        self.dialog.unlock_host()
        self.assertEqual(button.isEnabled(), before)

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


if __name__ == '__main__':
    unittest.main()
