"""Width-method selection and archive semantics; all PV access is mocked."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLCONFIGDIR', '/tmp/emit-width-test-mpl')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PyQt5.QtWidgets import QApplication
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/apps/emit_measure'))
from half_linac.src.apps.emit_measure import main


class WidthMethodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pv = patch.object(main.epics, 'caget', return_value=None).start()
        patch.object(main.epics, 'caput', return_value=1).start()
        self.addCleanup(patch.stopall)
        with patch.dict(os.environ, HALF_LINAC_MACHINE_ID='half', HALF_LINAC_CONTROL_BACKEND='vm'):
            self.window = main.myWindow()
        self.window.beam_image_timer.stop()
        self.window._beam_image_auto_refresh_ready = False
        self.addCleanup(self.window.close)

    def image(self, paras):
        width, height = paras.flag_pixel_shape
        x = np.linspace(-1, 1, width)
        y = np.linspace(-1, 1, height)
        # A narrow central distribution plus broad tails distinguishes the methods.
        px = np.exp(-x*x/.008) + .12*np.exp(-x*x/.15)
        py = np.exp(-y*y/.012) + .15*np.exp(-y*y/.2)
        return np.outer(py, px)

    def test_hidden_quad_scan_does_not_refresh_previous_screen(self):
        w = self.window
        w._beam_image_auto_refresh_ready = True
        w.beam_image_auto_refresh_checkbox.setChecked(True)
        w.tabWidget.setCurrentWidget(w.multi_screen_workspace)
        with patch.object(w, 'refresh_current_beam_image_fit') as refresh:
            w._auto_refresh_beam_image_fit()
            refresh.assert_not_called()

    def test_default_and_rms_preview_and_scan_use_the_same_width(self):
        w = self.window
        self.assertEqual(w._beam_width_method(), 'Gaussian fit')
        gaussian_paras = w.get_setting()
        image = self.image(gaussian_paras)
        self.pv.side_effect = lambda pv, *args, **kwargs: image.ravel() if pv == gaussian_paras.flagImagePV else None
        self.assertTrue(w.refresh_current_beam_image_fit(gaussian_paras))
        gaussian = w.latest_beam_fit_result
        w.beam_width_method_combo.setCurrentIndex(1)
        paras = w.get_setting()
        self.assertEqual(paras.beam_width_method, 'RMS moments')
        w.beam_image_overlays = True
        self.assertTrue(w.refresh_current_beam_image_fit(paras))
        rms = w.latest_beam_fit_result
        self.assertGreater(rms.sigx_mm, gaussian.sigx_mm * 1.4)
        self.assertIn('RMS moments', w.beam_fit_summary_label.text())
        self.assertIsNone(rms.x_projection.fitted_projection)
        paras.recal = False
        paras.samples = 1
        paras.settle_time = paras.sample_interval = 0
        paras.scan_strategy = 'grid'
        worker = main.scanThread(paras)
        worker.k1l, worker.sigxl, worker.sigyl = [], [], []
        observation = worker._acquire_k1(0)
        self.assertAlmostEqual(observation.sigx, rms.sigx_mm)
        self.assertAlmostEqual(observation.sigy, rms.sigy_mm)
        self.assertEqual(w._scan_metadata_from_paras(paras)['beam_width_method'], 'RMS moments')

    def test_archives_restore_method_and_old_archives_default_to_gaussian(self):
        w = self.window
        paras = w.get_setting()
        paras.scan_strategy = 'grid'
        paras.model_snapshot_metadata = {'fields': []}
        metadata = w._scan_metadata_from_paras(paras)
        metadata['beam_width_method'] = 'RMS moments'
        w._apply_scan_metadata_to_controls(metadata, 'test archive')
        self.assertEqual(w._beam_width_method(), 'RMS moments')
        del metadata['beam_width_method']
        w._apply_scan_metadata_to_controls(metadata, 'legacy archive')
        self.assertEqual(w._beam_width_method(), 'Gaussian fit')
        w._validate_scan_metadata(metadata, metadata, 'legacy archive')
        expected = dict(metadata, beam_width_method='RMS moments')
        with self.assertRaisesRegex(RuntimeError, 'cannot be converted'):
            w._validate_scan_metadata(metadata, expected, 'legacy archive')

    def test_method_locked_during_scan_and_enabled_afterwards(self):
        w = self.window
        w.scan = Mock(isRunning=Mock(return_value=True))
        w.scan_mode = 'scan'
        w._update_scan_run_controls()
        self.assertFalse(w.beam_width_method_combo.isEnabled())
        w.scan = None
        w.scan_mode = None
        w._update_scan_run_controls()
        self.assertTrue(w.beam_width_method_combo.isEnabled())


if __name__ == '__main__':
    unittest.main()
