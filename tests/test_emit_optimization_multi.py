"""Multi-solenoid motion, rollback, vector optimization, and archive tests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.apps.emit_measure.optimization import (
    OptimizationConfig, OptimizationVariable, OptimizationSession, EpicsVariableGroup, RestoreFailure,
)
from half_linac.src.optimization.emittance_rcds import optimize_currents
from half_linac.src.shared.machine_profile import load_app_context, resolve_channel
from test_emit_optimization import result


class MultiTests(unittest.TestCase):
    def setUp(self):
        self.context = load_app_context('emit_measure', machine_id='half', control_backend='real')
        # Deliberately use a nonalphabetical variable order and different bounds.
        self.config = OptimizationConfig((OptimizationVariable('SS02', 2, 10),
                                          OptimizationVariable('SS01', 1, 9)), 'x', 3,
                                         settle_time=0, motion_timeout=.01)
        self.values = {'SS01': 5., 'SS02': 6., 'SM01': 40.}
        self.channel_names = {resolve_channel(self.context, name, channel): name
                              for name in self.values for channel in ('current_set', 'current_readback')}
        self.writes = []
        self.fail_put = lambda name, value: False
        self.after_put = lambda name, value: None
        self.get_error = set()
        self.device = EpicsVariableGroup(self.context, self.config, get=self.get, put=self.put)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def get(self, pv, **kwargs):
        name = self.channel_names[pv]
        return None if name in self.get_error else self.values[name]

    def put(self, pv, value, **kwargs):
        name = self.channel_names[pv]
        self.writes.append((name, value))
        self.values[name] = value  # A failed put may have changed hardware.
        self.after_put(name, value)
        return -1 if self.fail_put(name, value) else 1

    def session(self, **kwargs):
        def optimizer(fn, low, high, initial, budget):
            self.assertEqual(initial, (6., 5.))
            self.assertEqual(low, (2., 1.))
            fn((4., 3.))
        measure = kwargs.pop('measure', lambda *_: result(
            1 + (self.values['SS01'] - 3)**2 + (self.values['SS02'] - 4)**2))
        return OptimizationSession(self.config, self.device, measure, Path(self.temp.name) / 'run',
                                   optimizer=kwargs.pop('optimizer', optimizer), **kwargs)

    def test_full_vector_verified_applied_and_restored(self):
        s = self.session()
        self.assertEqual(s.run()['status'], 'complete')
        self.assertTrue(s.confirmed)
        self.assertEqual(s.best['point'], (4., 3.))
        self.assertEqual(self.values, {'SS01': 5., 'SS02': 6., 'SM01': 40.})
        s.manual_move(best=True)
        self.assertEqual((self.values['SS02'], self.values['SS01']), (4., 3.))
        s.manual_move()
        self.assertEqual((self.values['SS02'], self.values['SS01']), (6., 5.))
        archive = json.loads((s.run_dir / 'optimization.json').read_text())
        self.assertEqual(archive['variable_order'], ['SS02', 'SS01'])
        self.assertEqual(archive['initial_currents'], {'SS02': 6., 'SS01': 5.})
        self.assertEqual(archive['best']['currents'], {'SS02': 4., 'SS01': 3.})
        self.assertTrue(all(v['restored'] for v in archive['device_restoration'].values()))
        self.assertNotIn('SM01', [name for name, _ in self.writes])

    def test_later_write_failure_restores_every_selected_supply(self):
        self.fail_put = lambda name, value: name == 'SS01' and value == 3
        s = self.session()
        self.assertEqual(s.run()['status'], 'failed')
        self.assertFalse(s.confirmed)
        self.assertTrue(s.summary['restored'])
        self.assertEqual(self.writes[-2:], [('SS02', 6.), ('SS01', 5.)])
        self.assertEqual(s.summary['motions'][-1]['status'], 'failed')
        self.assertEqual(len(s.records), 2)  # No measurement of a partially applied candidate.

    def test_first_restore_failure_does_not_skip_second_supply(self):
        self.device.validate()
        self.values.update(SS02=4., SS01=3.)
        self.fail_put = lambda name, value: name == 'SS02'
        with self.assertRaisesRegex(RestoreFailure, 'SS02'):
            self.device.restore((6., 5.))
        self.assertEqual(self.writes, [('SS02', 6.), ('SS01', 5.)])
        self.assertFalse(self.device.last_restore['SS02']['restored'])
        self.assertTrue(self.device.last_restore['SS01']['restored'])

    def test_restore_retry_still_attempts_others_when_one_readback_disconnected(self):
        s = self.session()
        s.run()
        self.get_error.add('SS02')
        self.writes.clear()
        with self.assertRaisesRegex(RestoreFailure, 'SS02'):
            s.manual_move()
        self.assertIn(('SS01', 5.), self.writes)
        self.assertEqual(s.summary['status'], 'restore_failed')
        self.assertFalse(s.confirmed)

    def test_cancel_between_writes_restores_all_without_measuring(self):
        cancel = Event()
        self.after_put = lambda name, value: cancel.set() if name == 'SS02' else None
        s = self.session(cancelled=cancel)
        self.assertEqual(s.run()['status'], 'stopped')
        self.assertEqual(s.records, [])
        self.assertEqual(self.writes, [('SS02', 6.), ('SS02', 6.), ('SS01', 5.)])
        self.assertTrue(s.summary['restored'])

    def test_all_bounds_checked_before_first_write(self):
        self.device.validate()
        with self.assertRaisesRegex(ValueError, 'SS01'):
            self.device.move((4., 20.), lambda: None)
        self.assertEqual(self.writes, [])
        with self.assertRaisesRegex(ValueError, 'dimension'):
            self.device.move((4.,), lambda: None)
        self.assertEqual(self.writes, [])

    def test_missing_member_connection_blocks_entire_run_before_writes(self):
        self.get_error.add('SS01')
        s = self.session()
        self.assertEqual(s.run()['status'], 'failed')
        self.assertEqual(self.writes, [])
        self.assertEqual(s.records, [])

    def test_quad_and_duplicate_variables_rejected(self):
        config = OptimizationConfig((OptimizationVariable('QL09', 1, 9),), 'x', 3)
        with self.assertRaisesRegex(ValueError, 'only solenoids'):
            EpicsVariableGroup(self.context, config, get=self.get, put=self.put).validate()
        config = OptimizationConfig((self.config.variables[0],) * 2, 'x', 3)
        with self.assertRaisesRegex(ValueError, 'unique'):
            config.validate()
        self.assertEqual(self.writes, [])

    def test_rcds_vector_normalization_and_search(self):
        points = []
        def evaluate(point):
            points.append(point)
            return (point[0] - 4)**2 + (point[1] - 3)**2
        optimize_currents(evaluate, (2., 1.), (10., 9.), (6., 5.), 60)
        self.assertEqual(points[0], (6., 5.))
        self.assertTrue(all(2 <= x <= 10 and 1 <= y <= 9 for x, y in points))
        self.assertLess(min((x - 4)**2 + (y - 3)**2 for x, y in points), .1)
        self.assertLessEqual(len(points), 60)

    def test_bo_vector_search_uses_full_budget_and_independent_bounds(self):
        from half_linac.src.optimization.emittance_bo import optimize_currents as optimize_bo
        points = []
        optimize_bo(
            lambda point: points.append(point) or (point[0] - 4)**2 + (point[1] - 3)**2,
            (2., 1.), (10., 9.), (6., 5.), 20,
        )
        self.assertEqual(points[0], (6., 5.))
        self.assertEqual(len(points), 20)
        self.assertTrue(all(2 <= x <= 10 and 1 <= y <= 9 for x, y in points))
        self.assertLess(min((x - 4)**2 + (y - 3)**2 for x, y in points), .1)


if __name__ == '__main__':
    unittest.main()
