"""Offscreen workbench checks; no IOC, Elegant or PV operations."""
import os
import sys
import time
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLCONFIGDIR', '/tmp/vm-workbench-test-mpl')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt5.QtCore import QLibraryInfo
from PyQt5.QtWidgets import QApplication
from half_linac.src.virtual_machine.common.mainVM import myWindow
from half_linac.src.virtual_machine.workbench_ui import load_observation
from half_linac.src.virtual_machine.workbench_data import observation_dir, save_observation


class WorkbenchGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        plugin = Path(QLibraryInfo.location(QLibraryInfo.PluginsPath))/'platforms/libqoffscreen.so'
        if not plugin.is_file():
            raise unittest.SkipTest('Use the half_linac Python environment for Qt offscreen tests')
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, machine='half'):
        with patch.dict(os.environ, HALF_LINAC_MACHINE_ID=machine, HALF_LINAC_CONTROL_BACKEND='vm'):
            window = myWindow()
        window.show()
        self.app.processEvents()
        window.process_timer.stop()
        self.addCleanup(window.close)
        self.addCleanup(self.app.processEvents)
        return window

    def test_both_machines_layout_themes_and_handoff(self):
        for machine in ('half', 'irfel'):
            window = self.make_window(machine)
            window.resize(1366, 768)
            self.app.processEvents()
            self.assertEqual((window.width(), window.height()), (1366, 768))
            self.assertEqual([window.tabs.tabText(i) for i in range(4)],
                             ['Devices', 'Beam Source', 'Lattice', 'Errors'])
            for index in range(4):
                window.tabs.setCurrentIndex(index)
                self.app.processEvents()
            for theme in ('light', 'dark'):
                window.current_theme = theme
                window._apply_theme()
                window._draw_curve()
                window._draw_screen()
            with patch.object(window, '_beam_source_is_segment_handoff', return_value=True):
                window._refresh_beam_source_availability()
                self.assertFalse(window.apply_beam_source_button.isEnabled())
            window.log_toggle.setChecked(True)
            self.assertFalse(window.textEdit.isHidden())
            window.close()

    def test_result_freshness_selection_and_publication_warning(self):
        window = self.make_window()
        window._executor.shutdown(wait=True)
        window._executor = Mock()
        window._executor.submit.return_value = Future()
        state = dict(lattice={'Q': dict(TYPE='QUAD', L='1')}, usedline=['Q', 'Q'])
        result = dict(session='new', calculation=1, input_version='a', curves={},
                      screens={'1': dict(name='W', s=2, error='Missing output')})
        status = dict(session='new', calculation=1, phase='Ready', result_version='a', publication={'bpm': False})
        window._session = 'new'
        window.processes['vm'] = Mock(poll=Mock(return_value=None))
        def refresh(version='a', current_status=status):
            future = Future()
            future.set_result((state, version, current_status, result))
            window._read_future = future
            window._refresh_process_state()
        refresh()
        self.assertIn('Latest Result', window.result_status.text())
        self.assertIn('Publication incomplete', window.result_status.text())
        selection = window.screen_choice.currentData()
        window.devices.setCurrentItem(window.devices.topLevelItem(1))
        self.assertEqual(window._selected_index, 1)
        self.assertEqual(window.screen_choice.currentData(), selection)
        refresh('b')
        self.assertIn('Out of Date', window.result_status.text())
        refresh('a', dict(status, session='old'))
        self.assertNotIn('Latest Result', window.result_status.text())
        refresh('a', dict(status, phase='Failed', error='Elegant failed'))
        self.assertIn('Last Successful Result', window.result_status.text())
        self.assertTrue(window.log_toggle.isChecked())
        window.processes.clear()

    def test_process_start_failure_exit_and_stop_timeout(self):
        window = self.make_window()
        with patch('half_linac.src.virtual_machine.workbench_ui.subprocess.Popen', side_effect=OSError('missing executable')):
            self.assertIsNone(window._start_process('vm', 'VM', ['missing'], '/tmp', True))
            self.assertTrue(window.log_toggle.isChecked())
        proc = window._start_process('vm', 'Test child', [sys.executable, '-c', 'print("child output")'], '/tmp', True)
        deadline = time.monotonic() + 3
        while proc.poll() is None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        window._refresh_process_state()
        self.assertNotIn('vm', window.processes)
        stubborn = Mock(poll=Mock(return_value=None))
        window.processes['vm'] = stubborn
        with patch.object(window, '_signal_process_group') as send:
            window._stop_subpro()
            self.assertTrue(window._stopping)
            window._stop_deadline = 0
            window._refresh_process_state()
            self.assertEqual(send.call_count, 2)
        window.processes.clear()

    def test_result_and_status_must_match(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)/'runtime.json'
            save_observation(runtime, dict(lattice={}, usedline=[]))
            directory = observation_dir(runtime)
            save_observation(directory/'status.json', dict(session='new', result='result.json',
                             result_version='a', calculation=1))
            save_observation(directory/'result.json', dict(session='old', input_version='a', calculation=1))
            self.assertIsNone(load_observation(runtime)[3])


if __name__ == '__main__':
    unittest.main()
