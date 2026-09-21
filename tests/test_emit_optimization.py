"""Offline optimization lifecycle tests. No IOC is required or contacted."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.apps.emit_measure.optimization import (
    OptimizationConfig, OptimizationVariable, OptimizationSession, EpicsVariableGroup, measurement_values,
    RestoreFailure, Stopped, VerifiedQuadRestore,
)
from half_linac.src.shared.machine_profile import load_app_context, resolve_channel


def single_config(name, low, high, plane, other_limit, **kwargs):
    return OptimizationConfig((OptimizationVariable(name, low, high),), plane, other_limit, **kwargs)


def result(x, y=2, **extra):
    return dict(restored=True, xplane=dict(status='valid', validation_status='validated', exn_raw=x),
                yplane=dict(status='valid', validation_status='validated', exn_raw=y), **extra)


class Device:
    def __init__(self):
        self.value = 5.
        self.moves = []
        self.restores = []
        self.fail_restore = False
    def validate(self, **kwargs):
        pass
    def initial(self):
        return (self.value,)
    def readback(self):
        return (self.value,)
    def move(self, value, check, **kwargs):
        value = value[0]
        check()
        self.moves.append(value)
        self.value = value
    def restore(self, value):
        value = value[0]
        self.restores.append(value)
        if self.fail_restore:
            raise RestoreFailure('SS01 initial=5, last=3')
        self.value = value


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.device = Device()
        self.config = single_config('SS01', 1, 9, 'x', 3, settle_time=0)

    def session(self, **kwargs):
        def optimizer(evaluate, low, high, initial, budget):
            for value in (5., 3., 2., 4.):
                evaluate(value)
        return OptimizationSession(self.config, self.device,
            kwargs.pop('measure', lambda path, check: result((self.device.value - 3)**2 + 1)),
            Path(self.temp.name) / 'run', optimizer=kwargs.pop('optimizer', optimizer), **kwargs)

    def test_improvement_verified_restored_and_explicitly_applied(self):
        s = self.session()
        self.assertEqual(s.run()['status'], 'complete')
        self.assertTrue(s.confirmed)
        self.assertEqual(s.best['currents']['SS01'], 3)
        self.assertEqual(self.device.value, 5)
        self.assertEqual(len(s.records), 8)
        self.assertTrue((s.run_dir / 'optimization.json').exists())
        self.assertEqual(len(list(s.run_dir.glob('measurement_*'))), 8)
        s.manual_move(best=True)
        self.assertEqual(self.device.value, 3)
        s.manual_move()
        self.assertEqual(self.device.value, 5)

    def test_constraint_penalty_and_best_excludes_infeasible(self):
        scores = []
        def optimizer(fn, *_):
            scores.extend([fn(3), fn(4)])
        s = self.session(optimizer=optimizer, measure=lambda *_: result(
            (self.device.value - 3)**2 + 1, 4 if self.device.value == 3 else 2))
        s.run()
        self.assertEqual(scores[0], 2)
        self.assertLess(scores[1], 1)
        self.assertEqual(s.best['currents']['SS01'], 4)

    def test_invalid_measurement_retried_once_then_aborts(self):
        calls = []
        def measure(*_):
            calls.append(1)
            return result(float('nan'))
        s = self.session(measure=measure)
        self.assertEqual(s.run()['status'], 'failed')
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.device.restores, [5])
        self.assertFalse(s.confirmed)

    def test_quad_restore_failure_is_not_retried(self):
        def measure(*_):
            return {'restored': False, 'restore_error': 'QL09 current stuck'}
        s = self.session(measure=measure)
        self.assertEqual(s.run()['status'], 'restore_failed')
        self.assertIn('QL09', s.summary['quad_restore_error'])
        self.assertEqual(len(s.records), 1)
        self.assertEqual(self.device.restores, [5])

    def test_cancel_during_measurement_restores_after_measurement_returns(self):
        cancel = Event()
        order = []
        def measure(path, check):
            cancel.set()
            order.append('quad restored')
            return result(1)
        original = self.device.restore
        def restore(value):
            order.append('solenoid restored')
            original(value)
        self.device.restore = restore
        s = self.session(measure=measure, cancelled=cancel)
        self.assertEqual(s.run()['status'], 'stopped')
        self.assertEqual(order, ['quad restored', 'solenoid restored'])
        self.assertFalse(s.confirmed)

    def test_time_exhaustion_never_forces_verification(self):
        clock = [0.]
        def measure(*_):
            clock[0] += 10000
            return result(1)
        s = self.session(measure=measure, clock=lambda: clock[0])
        self.assertEqual(s.run()['status'], 'budget_exhausted')
        self.assertEqual(len(s.records), 1)
        self.assertEqual(self.device.value, 5)

    def test_reserve_verification_and_count_retries(self):
        self.config = single_config('SS01', 1, 9, 'x', 3, max_measurements=7)
        def optimizer(fn, *_):
            for _ in range(100):
                fn(3)
        s = self.session(optimizer=optimizer)
        s.run()
        self.assertEqual(len(s.records), 7)
        self.assertEqual([r['stage'] for r in s.records[-2:]], ['Verify best'] * 2)
        self.assertTrue(s.confirmed)

    def test_restore_failure_cannot_be_applied(self):
        self.device.fail_restore = True
        s = self.session()
        self.assertEqual(s.run()['status'], 'restore_failed')
        self.assertFalse(s.confirmed)
        self.assertIn('last=3', s.summary['restore_error'])
        with self.assertRaises(ValueError):
            s.manual_move(best=True)

    def test_non_improving_verification_disables_apply(self):
        s = self.session(measure=lambda *_: result(1))
        s.run()
        self.assertFalse(s.confirmed)

    def test_rcds_offline_convergence_and_normalized_initial(self):
        from half_linac.src.optimization.emittance_rcds import optimize_currents
        seen = []
        optimize_currents(lambda point: seen.append(point[0]) or (point[0] - 3)**2, (1,), (9,), (5,), 25)
        self.assertEqual(seen[0], 5)
        self.assertTrue(all(1 <= x <= 9 for x in seen))
        self.assertLess(min(abs(x - 3) for x in seen), 0.2)
        self.assertLessEqual(len(seen), 25)

    def test_bo_offline_convergence_budget_bounds_and_repeatability(self):
        from half_linac.src.optimization.emittance_bo import optimize_currents
        runs = []
        for _ in range(2):
            seen = []
            optimize_currents(
                lambda point: seen.append(point) or (point[0] - 3) ** 2,
                (1,), (9,), (5,), 12,
            )
            runs.append(seen)
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[0][0], (5.,))
        self.assertEqual(len(runs[0]), 12)
        self.assertTrue(all(1 <= point[0] <= 9 for point in runs[0]))
        self.assertLess(min(abs(point[0] - 3) for point in runs[0]), .1)

    def test_algorithm_validation_and_archive_metadata(self):
        with self.assertRaisesRegex(ValueError, 'RCDS or BO'):
            single_config('SS01', 1, 9, 'x', 3, algorithm='unknown').validate()
        config = single_config('SS01', 1, 9, 'x', 3, algorithm='bo',
                               bo_initial_samples=4, bo_exploration=.05, bo_random_seed=7)
        session = OptimizationSession(
            config, self.device, lambda *_: result(1), Path(self.temp.name) / 'bo-run',
            optimizer=lambda fn, low, high, initial, budget: fn(initial),
        )
        session.run()
        self.assertEqual(session.summary['optimizer']['name'], 'bo')
        self.assertEqual(session.summary['optimizer']['acquisition'], 'expected improvement')
        self.assertEqual(session.summary['optimizer']['initial_samples'], 4)
        self.assertEqual(session.summary['optimizer']['exploration'], .05)
        self.assertEqual(session.summary['optimizer']['random_seed'], 7)

    def test_algorithm_parameter_validation_and_dynamic_bo_default(self):
        config = single_config('SS01', 1, 9, 'x', 3, algorithm='bo')
        self.assertEqual(config.effective_bo_initial_samples(), 3)
        with self.assertRaisesRegex(ValueError, 'search budget'):
            single_config('SS01', 1, 9, 'x', 3, max_measurements=6,
                          algorithm='bo').validate()
        with self.assertRaisesRegex(ValueError, 'initial step'):
            single_config('SS01', 1, 9, 'x', 3, rcds_initial_step=0).validate()
        with self.assertRaisesRegex(ValueError, 'exploration'):
            single_config('SS01', 1, 9, 'x', 3, bo_exploration=-.1).validate()
        with self.assertRaisesRegex(ValueError, 'random seed'):
            single_config('SS01', 1, 9, 'x', 3, bo_random_seed=-1).validate()

    def test_session_passes_effective_parameters_to_algorithm_adapters(self):
        bo = unittest.mock.Mock()
        bo_config = single_config('SS01', 1, 9, 'x', 3, algorithm='bo',
                                  bo_initial_samples=4, bo_exploration=.03, bo_random_seed=9)
        with patch('half_linac.src.optimization.emittance_bo.optimize_currents', bo):
            session = OptimizationSession(bo_config, self.device, None, Path(self.temp.name) / 'bo-params')
        session.optimizer(lambda *_: 0, (1,), (9,), (5,), 10)
        self.assertEqual(bo.call_args.kwargs, {
            'initial_samples': 4, 'exploration': .03, 'random_seed': 9,
        })

        rcds = unittest.mock.Mock()
        rcds_config = single_config('SS01', 1, 9, 'x', 3, rcds_initial_step=.35)
        with patch('half_linac.src.optimization.emittance_rcds.optimize_currents', rcds):
            session = OptimizationSession(rcds_config, self.device, None, Path(self.temp.name) / 'rcds-params')
        session.optimizer(lambda *_: 0, (1,), (9,), (5,), 10)
        self.assertEqual(rcds.call_args.kwargs, {'initial_step': .35})

    def test_fatal_scan_failure_is_not_retried(self):
        s = self.session(measure=lambda *_: {'restored': True, 'error': 'QL09 scan write failed', 'fatal': True})
        self.assertEqual(s.run()['status'], 'failed')
        self.assertEqual(len(s.records), 1)
        self.assertEqual(self.device.value, 5)

    def test_raw_emittance_used_without_display_rounding(self):
        r = result(.004)
        r['xplane']['exn'] = 0
        self.assertEqual(measurement_values(r)['x'], .004)

    def test_measurement_mode_controls_quality_requirement(self):
        plain = result(1)
        plain['xplane'].pop('validation_status')
        plain['yplane'].pop('validation_status')
        self.assertEqual(measurement_values(plain, 'grid')['x'], 1)
        with self.assertRaisesRegex(ValueError, 'adaptive quality'):
            measurement_values(plain, 'adaptive_quality')

    def test_baseline_constraint_failure_prevents_search(self):
        optimizer = unittest.mock.Mock()
        s = self.session(measure=lambda *_: result(1, 4), optimizer=optimizer)
        self.assertEqual(s.run()['status'], 'failed')
        optimizer.assert_not_called()
        self.assertEqual(len(s.records), 2)

    def test_invalid_retry_counts_toward_total_budget(self):
        calls = []
        def measure(*_):
            calls.append(1)
            return result(float('nan') if len(calls) == 1 else (self.device.value - 3)**2 + 1)
        s = self.session(measure=measure)
        s.run()
        self.assertEqual(len(s.records), 9)
        self.assertFalse(s.records[0]['valid'])
        self.assertEqual(s.records[1]['attempt'], 2)

    def test_invalid_initial_range_does_not_write(self):
        self.config = single_config('SS01', 1, 4, 'x', 3)
        s = self.session()
        self.assertEqual(s.run()['status'], 'failed')
        self.assertEqual(self.device.moves, [])
        self.assertEqual(self.device.restores, [])

    def test_quad_recovery_required_before_clearing_fault(self):
        recover = unittest.mock.Mock()
        s = self.session(measure=lambda *_: {'restored': False, 'restore_error': 'QL09 failed'}, recover_quad=recover)
        s.run()
        self.assertIn('quad_restore_error', s.summary)
        s.manual_move()
        recover.assert_called_once()
        self.assertNotIn('quad_restore_error', s.summary)
        self.assertEqual(s.summary['status'], 'initial_restored')

    def test_manual_write_failure_restores_and_invalidates_best(self):
        s = self.session()
        s.run()
        self.assertTrue(s.confirmed)
        self.device.move = unittest.mock.Mock(side_effect=RuntimeError('write failed'))
        with self.assertRaisesRegex(RuntimeError, 'write failed'):
            s.manual_move(best=True)
        self.assertFalse(s.confirmed)
        self.assertTrue(s.summary['restored'])
        self.assertEqual(self.device.value, 5)

    def test_motion_failure_after_possible_write_still_restores(self):
        def failed_move(value, check, **kwargs):
            self.device.value = 8
            raise RuntimeError('readback timed out')
        self.device.move = failed_move
        s = self.session()
        self.assertEqual(s.run()['status'], 'failed')
        self.assertEqual(self.device.value, 5)
        self.assertEqual(self.device.restores, [5])


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.context = load_app_context('emit_measure', machine_id='half', control_backend='real')
        self.config = single_config('SS01', 1, 9, 'x', 3, motion_timeout=.01, settle_time=0)

    def test_write_failure_identifies_device(self):
        d = EpicsVariableGroup(self.context, self.config, get=lambda *a, **k: 5., put=lambda *a, **k: -1)
        d.validate()
        with self.assertRaisesRegex(RuntimeError, 'SS01.*write failed'):
            d.move(4, lambda: None)

    def test_readback_timeout_identifies_target_and_last_value(self):
        d = EpicsVariableGroup(self.context, self.config, get=lambda *a, **k: 5., put=lambda *a, **k: 1)
        d.validate()
        with self.assertRaisesRegex(RuntimeError, 'target 4.*last readback 5'):
            d.move(4, lambda: None)

    def test_vm_rejected_without_pv_access(self):
        ctx = load_app_context('emit_measure', machine_id='half', control_backend='vm')
        def forbidden(*a, **k):
            raise AssertionError('PV access forbidden')
        with self.assertRaisesRegex(ValueError, 'HALF real'):
            EpicsVariableGroup(ctx, self.config, get=forbidden, put=forbidden).validate()

    def test_machine_bounds_validated_before_pv_access(self):
        bad = single_config('SS01', 1, 20, 'x', 3)
        with self.assertRaisesRegex(ValueError, 'machine limits'):
            EpicsVariableGroup(self.context, bad, get=lambda *a, **k: self.fail('PV read')).validate()

    def test_quad_restore_checks_physical_current(self):
        state = {'value': 5.}
        with patch('epics.caget', side_effect=lambda pv, **kw: state['value']), patch('epics.caput', return_value=1):
            restore = VerifiedQuadRestore(self.context, timeout=.01)
            state['value'] = 6.
            with self.assertRaisesRegex(RestoreFailure, 'QL09.*last readback=6'):
                restore(5)

    def test_selected_quad_motion_and_restore_use_only_its_channels(self):
        for name in ('QL09', 'QT02', 'QT23'):
            with self.subTest(quad=name):
                channels = {key: resolve_channel(self.context, name, key)
                            for key in ('K1', 'current_set', 'current_readback')}
                values = {pv: 5. for pv in channels.values()}
                def put(pv, value, **kwargs):
                    self.assertEqual(pv, channels['K1'])
                    values[pv] = value
                    return 1
                with patch('epics.caget', side_effect=lambda pv, **kw: values[pv]), \
                     patch('epics.caput', side_effect=put) as write:
                    restore = VerifiedQuadRestore(self.context, name, timeout=.01)
                    restore.move(6., lambda: None)
                    restore(5.)
                    self.assertEqual(values[channels['K1']], 5.)
                    self.assertEqual(write.call_count, 2)


if __name__ == '__main__':
    unittest.main()
