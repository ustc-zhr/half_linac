from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from half_linac.src.apps.solenoid_centering.joint import JointScanner, load_joint_plans, solve_joint_step
from half_linac.src.apps.solenoid_centering.scan import StopRequested, StateDriftError, RestoreFailed
from half_linac.src.shared.machine_profile import load_app_context


class CoupledIO:
    """Synthetic centroid model with cross-coupled correctors and fixed BPM offsets."""
    def __init__(self, scanner):
        self.scanner = scanner
        self.values = {t.pv_name: (5. if e in scanner.solenoids or t.element_kind == 'solenoid' else 0.)
                       for e, t in scanner.devices.items()}
        self.initial = dict(self.values)
        self.readbacks = {scanner.readbacks[e]: t.pv_name for e, t in scanner.devices.items()}
        self.writes = []
        self.fail_at = None
        self.nonfinite = False
        self.zero_response = False
        self.curvature = False
        n = len(scanner.correctors)
        rng = np.random.default_rng(4)
        # Every solenoid responds in both planes to several steering channels.
        self.matrix = rng.normal(size=(len(scanner.solenoids), 2, n))
        self.goal = np.linspace(-0.12, 0.12, n)

    def read(self, pv):
        if pv in self.readbacks:
            return self.values[self.readbacks[pv]]
        if pv in self.values:
            return self.values[pv]
        for b, channels in self.scanner.bpms.items():
            if pv not in channels:
                continue
            if self.nonfinite:
                return float('nan')
            axis = channels.index(pv)
            c = np.array([self.values[self.scanner.devices[e].pv_name] for e in self.scanner.correctors])
            value = 2. + axis  # Static BPM offset must not affect centering.
            for j, e in enumerate(self.scanner.solenoids):
                current_pv = self.scanner.devices[e].pv_name
                change = self.values[current_pv] - self.initial[current_pv]
                residual = self.matrix[j, axis] @ ((np.zeros_like(c) if self.zero_response else c) - self.goal)
                value += change * residual
                if self.curvature:
                    value += 10000 * change ** 2 * float(c @ c)
            return value / self.scanner.scale
        raise KeyError(pv)

    def write(self, pv, value):
        self.writes.append((pv, value))
        self.values[pv] = value
        if self.fail_at == len(self.writes):
            raise RuntimeError('write failed after reaching device')


class JointTests(unittest.TestCase):
    def setUp(self):
        self.context = load_app_context('solenoid_centering', machine_id='half', control_backend='real')
        self.plan = load_joint_plans(self.context)[0]
        self.scanner = JointScanner(self.context, self.plan, io=object())
        self.io = CoupledIO(self.scanner)
        self.scanner.io = self.scanner.helper.io = self.io
        self.scanner.helper._sleep = lambda _: self.scanner.helper._raise_if_stopped()
        self.writer = patch('half_linac.src.apps.solenoid_centering.joint.write_scan_result',
                            return_value=Path('/tmp/joint-test.json'))
        self.writer.start()
        self.addCleanup(self.writer.stop)

    def test_half_groups_and_corrector_mapping(self):
        self.assertEqual(len(self.scanner.correctors), 6)
        self.assertEqual(self.scanner.correctors[-2:], ('SL01-DX', 'SL01-DY'))
        five = JointScanner(self.context, load_joint_plans(self.context)[1], io=object())
        self.assertEqual(len(five.correctors), 10)

    def test_preflight_is_read_only_and_limits_are_enforced(self):
        report = self.scanner.preflight()
        self.assertGreater(report['estimated_minimum_seconds'], 0)
        self.assertEqual(self.io.writes, [])
        pv = self.scanner.devices[self.scanner.correctors[0]].pv_name
        self.io.values[pv] = 4.9
        with self.assertRaises(ValueError):
            self.scanner.preflight()
        self.assertEqual(self.io.writes, [])

    def test_coupled_fit_full_validation_apply_restore(self):
        result = self.scanner.run()
        self.assertTrue(result['recommendation_available'])
        self.assertGreater(result['relative_improvement'], 0.9)
        self.assertEqual(self.io.values, self.io.initial)
        self.assertEqual(result['restore'], 'verified')
        self.assertTrue(all(abs(v) <= self.plan.max_excursion_a for v in result['recommended'].values()))
        self.scanner.apply(result)
        self.assertTrue(result['applied'])
        self.scanner.restore_applied(result)
        self.assertEqual(self.io.values, self.io.initial)
        self.assertFalse(result['applied'])

    def test_stop_restores_and_disables_recommendation(self):
        self.scanner.helper.stop_requested = lambda: len(self.io.writes) >= 4
        with self.assertRaises(StopRequested):
            self.scanner.run()
        self.assertEqual(self.io.values, self.io.initial)
        self.assertFalse(self.scanner.last_result['recommendation_available'])

    def test_write_failure_after_motion_restores(self):
        self.io.fail_at = 7
        with self.assertRaisesRegex(RuntimeError, 'write failed'):
            self.scanner.run()
        self.assertEqual(self.io.values, self.io.initial)

    def test_five_point_validation_rejects_hidden_curvature(self):
        self.io.curvature = True
        result = self.scanner.run()
        self.assertTrue(any(item['accepted'] for item in result['iterations']))
        self.assertFalse(result['recommendation_available'])
        self.assertEqual(self.io.values, self.io.initial)

    def test_failed_apply_rolls_back_all_touched_correctors(self):
        result = self.scanner.run()
        self.io.fail_at = len(self.io.writes) + 3
        with self.assertRaisesRegex(RuntimeError, 'write failed'):
            self.scanner.apply(result)
        self.assertEqual(self.io.values, self.io.initial)
        self.assertEqual(result['restore'], 'verified')

    def test_changed_preflight_baseline_prevents_scan_writes(self):
        self.scanner.prepared_state = self.scanner.preflight()['original']
        self.io.values[self.scanner.devices['XC02'].pv_name] += 0.1
        with self.assertRaises(StateDriftError):
            self.scanner.run()
        self.assertEqual(self.io.writes, [])

    def test_nonfinite_bpm_rejected_without_writes(self):
        self.io.nonfinite = True
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            self.scanner.run()
        self.assertEqual(self.io.writes, [])

    def test_apply_rejects_changed_unselected_optics(self):
        result = self.scanner.run()
        pv = self.scanner.devices['SS01'].pv_name
        self.io.values[pv] += 0.1
        count = len(self.io.writes)
        with self.assertRaises(StateDriftError):
            self.scanner.apply(result)
        self.assertEqual(len(self.io.writes), count)

    def test_zero_corrector_response_cannot_recommend(self):
        self.io.zero_response = True
        result = self.scanner.run()
        self.assertEqual(result['diagnostics']['rank'], 0)
        self.assertFalse(result['recommendation_available'])
        self.assertEqual(self.io.values, self.io.initial)

    def test_individual_degradation_rejected_despite_total_improvement(self):
        accepted, improvement = self.scanner._acceptable([1, 0.1], [0.1, 0.2])
        self.assertGreater(improvement, 0.5)
        self.assertFalse(accepted)

    def test_svd_suppresses_nearly_null_direction(self):
        step, report = solve_joint_step(np.diag([1., 1e-10]), [1, 1], cutoff=0.02, damping=0.05)
        self.assertEqual(report['rank'], 1)
        self.assertEqual(step[1], 0)

    def test_restore_failure_is_recorded_and_blocks_result(self):
        original_restore = self.scanner._restore
        def fail(state):
            original_restore(state)
            raise RestoreFailed('simulated failed restore verification')
        self.scanner._restore = fail
        with self.assertRaises(RestoreFailed):
            self.scanner.run()
        self.assertEqual(self.scanner.last_result['restore'], 'failed')
        self.assertFalse(self.scanner.last_result['recommendation_available'])

    def test_plan_rejects_bad_numeric_options(self):
        for changes in ({'probe_a': float('nan')}, {'max_iterations': 0}, {'probe_a': 1.}):
            with self.assertRaises(ValueError):
                replace(self.plan, **changes).validate()


if __name__ == '__main__':
    unittest.main()
