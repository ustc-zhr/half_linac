"""Offline Qt integration: all PV reads/subscriptions are replaced with a fake."""
import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLCONFIGDIR', '/tmp/vm-v2-test-mpl')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

from PyQt5.QtCore import QLibraryInfo
from PyQt5.QtWidgets import QApplication, QDialog
from half_linac.src.virtual_machine.common.mainVM import myWindow
from half_linac.src.virtual_machine.beam_source import bootstrap_runtime_state
from half_linac.src.virtual_machine.workbench_data import occurrences, input_version
from half_linac.src.virtual_machine.magnet_control import model_value, capture_baseline


class FakeClient:
    def __init__(self, notify):
        self.notify = notify
        self.values = {}
    def subscribe(self, magnets):
        for magnet in magnets:
            self.notify(magnet.pv, self.values.get(magnet.pv))
    def read(self, magnet):
        return self.values[magnet.pv]
    def close(self):
        pass


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        plugin = Path(QLibraryInfo.location(QLibraryInfo.PluginsPath))/'platforms/libqoffscreen.so'
        if not plugin.is_file():
            raise unittest.SkipTest('Qt offscreen plugin unavailable')
        cls.app = QApplication.instance() or QApplication([])

    def window(self, machine='half'):
        with (patch.dict(os.environ, HALF_LINAC_MACHINE_ID=machine, HALF_LINAC_CONTROL_BACKEND='vm'),
              patch('half_linac.src.virtual_machine.magnet_panel.VmClient', FakeClient)):
            w = myWindow()
        w.process_timer.stop()
        w.show()
        self.app.processEvents()
        self.addCleanup(w.close)
        s = bootstrap_runtime_state(w.runtime)
        p = w.magnet_panel
        w._elements = occurrences(s)
        p.refresh(s, {}, None, False)
        p.enabled_session = True
        for magnet in p.magnets:
            value = model_value(s, magnet)
            p.controller.client.values[magnet.pv] = value
            p._pv_changed(magnet.pv, value)
        return w, p, s

    def choose(self, w, p, magnet):
        occurrence = next(e for e in w._elements if e['name'] == magnet.element_id)
        p.select(occurrence)

    def test_both_editors_units_curves_drafts_and_disconnect(self):
        for machine in ('half', 'irfel'):
            w, p, s = self.window(machine)
            w.resize(1366, 768)
            self.app.processEvents()
            self.assertEqual((w.width(), w.height()), (1366, 768))
            q = next(m for m in p.magnets if m.kind == 'quad')
            c = next(m for m in p.magnets if m.kind == 'corr')
            w.screen_choice.addItem('Keep this screen', 'screen')
            self.choose(w, p, q)
            self.assertEqual(w.curve_choice.currentText(), 'Twiss')
            p._edit(str(model_value(s, q) + .01))
            draft_text = p.value_edit.text()
            self.assertTrue(p.apply_button.isEnabled())
            self.choose(w, p, c)
            self.assertEqual(w.curve_choice.currentText(), 'Orbit')
            self.assertIn('mrad', p.title.text())
            self.assertEqual(w.screen_choice.currentData(), 'screen')
            self.choose(w, p, q)
            self.assertEqual(p.value_edit.text(), draft_text)
            p._pv_changed(q.pv, model_value(s, q) + .02)
            self.assertIn('External Change', p.message.text())
            self.assertFalse(p.apply_button.isEnabled())
            p.discard()
            self.assertIsNone(p._draft())
            p._pv_changed(q.pv, None)
            self.assertFalse(p.value_edit.isEnabled())
            w.close()

    def test_baseline_overlay_metrics_invalidation_and_themes(self):
        w, p, state = self.window()
        version = input_version(state)
        result = dict(session='s', calculation=1, input_version=version, input_state=copy.deepcopy(state),
            curves={'Orbit': dict(s=[0, 1], x=[0, 1], y=[0, 2], unit='mm')},
            screens={'screen': dict(cx=1., cy=2., sx=3., sy=4.)})
        status = dict(session='s', calculation=1, phase='Ready', result_version=version)
        p.baseline = capture_baseline(result, state, status, 's', p.magnets, p.profile_key, p.controller.client.read)
        p.compatible = True
        w._result = copy.deepcopy(result)
        w._result['screens']['screen']['cx'] = 1.5
        w.screen_choice.addItem('Screen', 'screen')
        p.show_baseline.setChecked(True)
        w._draw_curve()
        self.assertEqual(len(w.curve_plot.ax.lines), 4)
        self.assertIn('+0.5', w.screen_metrics.text())
        for theme in ('light', 'dark'):
            w.current_theme = theme
            w._apply_theme()
            p.redraw()
        changed = copy.deepcopy(state)
        changed['control']['run_setup']['random_number_seed'] = '99999'
        p.refresh(changed, status, result, False)
        self.assertFalse(p.show_baseline.isChecked())
        self.assertFalse(p.restore_button.isEnabled())
        self.assertEqual(p.baseline_label.text(), 'Baseline incompatible')
        w.close()

    def test_restore_confirmation_cannot_be_blocked_by_background_poll(self):
        w, p, state = self.window()
        version = input_version(state)
        result = dict(session='s', calculation=1, input_version=version,
                      input_state=copy.deepcopy(state), curves={}, screens={})
        status = dict(session='s', calculation=1, phase='Ready', result_version=version)
        p.baseline = capture_baseline(result, state, status, 's', p.magnets,
                                      p.profile_key, p.controller.client.read)
        p.compatible = True
        m = p.magnets[0]
        old = model_value(state, m)
        payload = dict(items=[(m, old, old + .001)], initial=state, session='s')

        def review():
            self.assertTrue(p.busy)
            # A timer refresh while the modal confirmation is open must not
            # occupy the single-worker controller needed by the confirmed write.
            p.refresh(state, status, result, True)
            self.assertFalse(p.controller.pending)
            return QDialog.Accepted

        with (patch('half_linac.src.virtual_machine.magnet_panel.QDialog.exec_', side_effect=review),
              patch.object(p, '_start') as start):
            p._confirm_restore(payload)
            start.assert_called_once()
            self.assertEqual(start.call_args.args[0], 'operation')
        with (patch('half_linac.src.virtual_machine.magnet_panel.QDialog.exec_', return_value=QDialog.Rejected),
              patch.object(p, '_start') as start):
            p._confirm_restore(payload)
            start.assert_not_called()
        self.assertFalse(p.busy)
        w.close()

    def test_external_change_stays_visible_for_invalid_draft(self):
        w, p, state = self.window()
        m = p.magnets[0]
        self.choose(w, p, m)
        p._edit('-')
        p._pv_changed(m.pv, model_value(state, m) + .01)
        self.assertIn('External Change', p.message.text())
        self.assertFalse(p.apply_button.isEnabled())
        w.close()

    def test_step_only_changes_draft_and_details_do_not_force_curve_refresh(self):
        w, p, state = self.window()
        magnet = next(m for m in p.magnets if m.kind == 'corr')
        self.choose(w, p, magnet)
        p.step_edit.setText('0.02')
        p._step_changed('0.02')
        before = p.controller.client.values[magnet.pv]
        p._step(1)
        self.assertAlmostEqual(float(p.value_edit.text()), before*1000 + .02)
        self.assertEqual(p.controller.client.values[magnet.pv], before)
        w.curve_choice.setCurrentText('Beam Size')
        p.refresh(state, {}, None, False)
        self.assertEqual(w.curve_choice.currentText(), 'Beam Size')
        self.assertEqual(p.step_edit.text(), '0.02')
        w.close()


if __name__ == '__main__':
    unittest.main()
