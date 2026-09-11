"""No PV/IOC connection: exercise VM writes, version acknowledgement and baseline."""
import copy
import sys
import unittest
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

from half_linac.src.virtual_machine.magnet_control import (
    Magnet, MagnetOperation, magnets_for, model_value, capture_baseline,
)
from half_linac.src.virtual_machine.workbench_data import input_version
from half_linac.src.shared.machine_profile import resolve_machine_runtime
from half_linac.src.virtual_machine.beam_source import bootstrap_runtime_state

Q = Magnet('Q', 'K1', 'VM:Q', 'quad', 1, 'm⁻²', -10, 10)
C = Magnet('C', 'KICK', 'VM:C', 'corr', 1000, 'mrad', -.01, .01)


def state():
    return dict(lattice={'Q': dict(TYPE='QUAD', K1='1', L='1'),
                         'C': dict(TYPE='HKICK', KICK='0', L='0')},
                usedline=['Q', 'C', 'Q'], control={'run_setup': {'random_number_seed': '123'}})


class Rig:
    def __init__(self):
        self.state = state()
        self.pvs = {'VM:Q': 1, 'VM:C': 0}
        self.time = 0
        self.writes = []
        self.after_write = lambda: None
        self.on_sleep = lambda: None
        self.sync = True
        self.cancel = False
        self.phase = 'Ready'
        self.session = 's'
        self.events = []

    def read(self, magnet):
        value = self.pvs[magnet.pv]
        if value is None:
            raise ConnectionError('disconnected')
        return value

    def write(self, magnet, value, timeout):
        self.writes.append((magnet.pv, value))
        self.pvs[magnet.pv] = value
        if self.sync:
            self.state['lattice'][magnet.element_id][magnet.field] = str(value)
        self.after_write()

    def sleep(self, seconds):
        self.time += seconds
        self.on_sleep()

    def status(self):
        return dict(session=self.session, phase=self.phase, result_version=input_version(self.state), input_version=input_version(self.state),
                    publication={'screens': False}, error='simulation failed' if self.phase == 'Failed' else None)

    def execute(self, items=None):
        return MagnetOperation(self, lambda: copy.deepcopy(self.state), self.status,
            emit=lambda *args: self.events.append(args), cancelled=lambda: self.cancel,
            monotonic=lambda: self.time, sleep=self.sleep).execute(
                items or [(Q, 1, 2)], copy.deepcopy(self.state), 's')


class MagnetTests(unittest.TestCase):
    def test_profile_channels_for_both_machines_are_vm_and_deduplicated(self):
        for machine in ('half', 'irfel'):
            runtime = resolve_machine_runtime(machine)
            source = bootstrap_runtime_state(runtime)
            magnets = magnets_for(runtime.profile, source)
            self.assertTrue(any(m.kind == 'quad' for m in magnets))
            self.assertTrue(any(m.kind == 'corr' for m in magnets))
            self.assertEqual(len(magnets), len({m.pv for m in magnets}))
            for m in magnets:
                self.assertEqual(m.scale, 1000 if m.kind == 'corr' else 1)
                self.assertIsInstance(model_value(source, m), float)

    def test_invalid_beam_input_does_not_create_runtime_files(self):
        from half_linac.src.virtual_machine.beam_source import apply_beam_source_config, BeamSourceError
        runtime = resolve_machine_runtime('irfel')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'runtime.json'
            runtime = replace(runtime, vm=replace(runtime.vm, runtime_json=path))
            with self.assertRaises(BeamSourceError):
                apply_beam_source_config(runtime, {})
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_limits_and_nonfinite_inputs(self):
        for value in (float('nan'), float('inf'), 11):
            with self.assertRaises(ValueError):
                Q.validate(value)
        self.assertEqual(C.validate(2/1000), .002)

    def test_success_requires_full_chain_and_reports_publication_separately(self):
        rig = Rig()
        result = rig.execute()
        self.assertEqual(result['phase'], 'Applied')
        self.assertEqual([p for p, _ in rig.events], ['Writing PV', 'Waiting for Model', 'Calculating'])
        self.assertFalse(result['publication']['screens'])
        self.assertEqual(result['version'], input_version(rig.state))

    def test_pv_success_does_not_imply_model_ack(self):
        rig = Rig()
        rig.sync = False
        result = rig.execute()
        self.assertEqual(result['phase'], 'Failed')
        self.assertIn('synchronization timed out', result['detail'])
        self.assertEqual(len(rig.writes), 1)
        self.assertGreaterEqual(rig.time, 5)

    def test_disconnect_or_external_pv_prevents_write(self):
        for value in (None, 4):
            rig = Rig()
            rig.pvs['VM:Q'] = value
            result = rig.execute()
            self.assertIn(result['phase'], ('Failed', 'Superseded'))
            self.assertEqual(rig.writes, [])

    def test_external_other_input_supersedes_even_during_model_wait(self):
        rig = Rig()
        rig.after_write = lambda: rig.state['control'].update(extra='changed')
        self.assertEqual(rig.execute()['phase'], 'Superseded')

    def test_failed_simulation_and_session_change_not_applied(self):
        rig = Rig()
        rig.phase = 'Failed'
        self.assertEqual(rig.execute()['phase'], 'Failed')
        rig = Rig()
        rig.session = 'another'
        self.assertEqual(rig.execute()['phase'], 'Superseded')

    def test_restore_stops_after_conflict_without_rollback(self):
        rig = Rig()
        rig.after_write = lambda: rig.pvs.update({'VM:C': .003})
        result = rig.execute([(Q, 1, 2), (C, 0, .001)])
        self.assertEqual(result['phase'], 'Superseded')
        self.assertEqual(rig.writes, [('VM:Q', 2)])
        self.assertEqual(result['items'][0]['status'], 'Model confirmed')
        self.assertEqual(result['items'][1]['status'], 'Not executed')

    def test_cancel_after_write_keeps_actual_values(self):
        rig = Rig()
        rig.after_write = lambda: setattr(rig, 'cancel', True)
        result = rig.execute([(Q, 1, 2), (C, 0, .001)])
        self.assertEqual(result['phase'], 'Cancelled')
        self.assertEqual(rig.pvs['VM:Q'], 2)
        self.assertEqual(len(rig.writes), 1)

    def test_old_failure_does_not_fail_a_new_input(self):
        rig = Rig()
        old = input_version(rig.state)
        normal_status = rig.status
        rig.status = lambda: (dict(session='s', phase='Failed', input_version=old)
                              if rig.time < .2 else normal_status())
        self.assertEqual(rig.execute()['phase'], 'Applied')

    def test_stopped_session_prevents_any_write(self):
        rig = Rig()
        rig.phase = 'Stopped'
        self.assertEqual(rig.execute()['phase'], 'Superseded')
        self.assertEqual(rig.writes, [])

    def test_calculation_is_not_limited_by_pv_timeout(self):
        rig = Rig()
        rig.phase = 'Calculating'
        def advance():
            if rig.time > 10:
                rig.phase = 'Ready'
        rig.on_sleep = advance
        self.assertEqual(rig.execute()['phase'], 'Applied')
        self.assertGreater(rig.time, 10)


class BaselineTests(unittest.TestCase):
    def capture(self):
        source = state()
        version = input_version(source)
        result = dict(session='s', calculation=3, input_version=version, input_state=copy.deepcopy(source), curves={}, screens={})
        status = dict(session='s', calculation=3, phase='Ready', result_version=version)
        baseline = capture_baseline(result, source, status, 's', [Q, C], 'profile',
                                    lambda m: model_value(source, m))
        return baseline, source, status

    def test_only_supported_magnet_fields_can_change(self):
        base, current, _ = self.capture()
        current['lattice']['Q']['K1'] = '3'
        current['lattice']['C']['KICK'] = '.002'
        self.assertTrue(base.compatible(current, [Q, C], 'profile'))
        for modify in (lambda s: s['usedline'].reverse(),
                       lambda s: s['control']['run_setup'].update(random_number_seed='456'),
                       lambda s: s['lattice']['Q'].update(DX='1e-6'),
                       lambda s: s['control'].update(sdds_beam={'input': 'other.bun'})):
            changed = copy.deepcopy(current)
            modify(changed)
            # Reverse the palindromic sample differently for a real topology change.
            if changed == current:
                changed['usedline'].append('C')
            self.assertFalse(base.compatible(changed, [Q, C], 'profile'))
        self.assertFalse(base.compatible(current, [Q, C], 'changed-profile'))

    def test_stale_or_pv_mismatched_capture_rejected(self):
        base, current, status = self.capture()
        for result, read in ((dict(base.result, session='old'), lambda m: model_value(current, m)),
                             (base.result, lambda m: 999)):
            with self.assertRaises(ValueError):
                capture_baseline(result, current, status, 's', [Q, C], 'profile', read)
        current['lattice']['Q']['K1'] = '2'
        with self.assertRaises(ValueError):
            capture_baseline(base.result, current, status, 's', [Q, C], 'profile', lambda m: model_value(current, m))


if __name__ == '__main__':
    unittest.main()
