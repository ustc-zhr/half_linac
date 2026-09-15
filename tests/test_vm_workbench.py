from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from half_linac.src.virtual_machine import workbench_data as data
from half_linac.src.virtual_machine.common import start_VM, err_gene_VM
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state


def state():
    return dict(lattice={'Q': {'NAME': 'Q', 'TYPE': 'QUAD', 'L': '1'},
                         'W': {'NAME': 'W', 'TYPE': 'WATCH', 'FILENAME': 'w.out', 'MODE': 'COORD'}},
                usedline=['Q', 'W', 'Q'], control={'run_setup': {}, 'error_element': {}})


class ResultTests(unittest.TestCase):
    def test_repeated_elements_have_distinct_positions(self):
        elements = data.occurrences(state())
        self.assertEqual([item['index'] for item in elements], [0, 1, 2])
        self.assertEqual([item['s'] for item in elements], [0, 1, 1])

    def test_named_columns_last_page_units_and_population_rms(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('one.cen', 'one.twi', 'one.sig', 'w.out'):
                (root / name).touch()
            dataset = SimpleNamespace(columnName=['Cy', 's', 'Cx', 'x', 'y'],
                columnData=[[[.002, .004]], [[0, 1]], [[.001, .003]],
                            [[-.001, .001]], [[-.002, .002]]], load=Mock())
            with patch('half_linac.src.shared.elegant_backend.parser._new_legacy_sdds_dataset', return_value=dataset):
                result = data.collect_results(state(), root)
            self.assertEqual(result['curves']['Orbit']['x'], [1, 3])
            self.assertEqual(result['curves']['Orbit']['y'], [2, 4])
            self.assertIn('error', result['curves']['Beam Size'])
            self.assertEqual(result['screens']['1']['sx'], 1)
            self.assertEqual(result['screens']['1']['sy'], 2)

    def test_new_parameters_units_and_single_load_per_file(self):
        values = dict(s=[0, 1], pCentral=[10, 20], Cdelta=[.1, 0],
                      Particles=[100, 75], St=[2e-12, 1e-12], Sdelta=[.011, .02],
                      enx=[2e-6, 3e-6], eny=[1e-6, 2e-6],
                      ex=[2e-7, 1e-7], ey=[1e-7, .5e-7],
                      ecnx=[1e-6, 2e-6], ecny=[.5e-6, 1e-6],
                      ecx=[1e-7, .5e-7], ecy=[.5e-7, .25e-7],
                      Sxp=[.001, .002], Syp=[.003, .004],
                      Cxp=[-.001, .002], Cyp=[.003, -.004])
        dataset = SimpleNamespace(columnName=list(values),
            columnData=[[v] for v in values.values()], load=Mock())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for suffix in ('cen', 'sig', 'twi'):
                (root / f'one.{suffix}').touch()
            with patch('half_linac.src.shared.elegant_backend.parser._new_legacy_sdds_dataset', return_value=dataset):
                curves = data.collect_results(dict(lattice={}, usedline=[]), root)['curves']
            self.assertEqual(dataset.load.call_count, 3)
        self.assertEqual(curves['Transmission']['x'], [100, 75])
        self.assertEqual(curves['RMS Bunch Length']['x'], [2, 1])
        self.assertAlmostEqual(curves['Relative Momentum Spread']['x'][0], 1)
        self.assertAlmostEqual(curves['Kinetic Energy']['x'][0], .51099895 * (122**.5 - 1))
        self.assertNotIn('y', curves['Kinetic Energy'])
        self.assertEqual(curves['Normalized Emittance']['x'], [2, 3])
        self.assertAlmostEqual(curves['Geometric Emittance']['x'][0], .2)
        self.assertAlmostEqual(curves['Geometric Emittance (corrected)']['x'][0], .1)
        self.assertEqual(curves['Normalized Emittance (corrected)']['x'], [1, 2])
        self.assertEqual(curves['Orbit Angle']['x'], [-1, 2])
        self.assertEqual(curves['RMS Divergence']['y'], [3, 4])

    def test_missing_and_stale_outputs_are_not_zero_curves(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'one.cen').touch()
            os.utime(root / 'one.cen', (1, 1))
            result = data.collect_results(state(), root, newer_than=2)
            self.assertIn('not refreshed', result['curves']['Orbit']['error'])
            self.assertIn('Missing output', result['curves']['Twiss']['error'])

    def test_empty_and_nonfinite_columns_rejected(self):
        with tempfile.NamedTemporaryFile() as file:
            for values in ([], [float('nan')]):
                dataset = SimpleNamespace(columnName=['x'], columnData=[[values]], load=Mock())
                with patch('half_linac.src.shared.elegant_backend.parser._new_legacy_sdds_dataset', return_value=dataset):
                    with self.assertRaises(ValueError):
                        data.columns(file.name, ['x'])


class RuntimeTests(unittest.TestCase):
    def test_new_input_during_run_queues_frozen_latest_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            live = root / 'runtime.json'
            initial = state()
            changed = copy.deepcopy(initial)
            changed['lattice']['Q']['L'] = '2'
            write_runtime_state(live, initial)
            runtime = SimpleNamespace(profile=object(), vm=SimpleNamespace(
                runtime_json=live, bootstrap_lattice=root/'initial.lte',
                bootstrap_ele=root/'initial.ele', line_name='ALL'))
            parser = SimpleNamespace(lattice={})
            frozen_inputs = []
            def run(parser, publisher, plan, elegant_dir, frozen):
                snapshot = read_runtime_state(frozen)
                frozen_inputs.append(snapshot)
                if len(frozen_inputs) == 1:
                    write_runtime_state(live, changed)
                    self.assertEqual(read_runtime_state(frozen), snapshot)
                else:
                    start_VM._stop_requested = True
                return {'bpm': False}
            with (patch.object(start_VM, 'resolve_machine_runtime', return_value=runtime),
                  patch.object(start_VM, 'ElegantParser', return_value=parser),
                  patch.object(start_VM, 'build_vm_publish_plan', return_value=SimpleNamespace(watch_scalar_specs=())),
                  patch.object(start_VM, 'VmPublisher'),
                  patch.object(start_VM, 'describe_runtime_usedline', return_value='ALL'),
                  patch.object(start_VM, '_update_vm_outputs', side_effect=run),
                  patch.object(start_VM, 'collect_results', return_value={'curves': {}, 'screens': {}}),
                  patch.object(start_VM.signal, 'signal')):
                start_VM._stop_requested = False
                try:
                    self.assertEqual(start_VM.main(), 0)
                finally:
                    start_VM._stop_requested = False
            self.assertEqual([item['lattice']['Q']['L'] for item in frozen_inputs], ['1', '2'])
            self.assertEqual(frozen_inputs[0]['control']['run_setup']['sigma'], '%s.sig')
            self.assertNotIn('sigma', read_runtime_state(live)['control']['run_setup'])
            status = read_runtime_state(data.observation_dir(live)/'status.json')
            self.assertEqual(status['result_version'], data.input_version(changed))
            self.assertFalse(status['publication']['bpm'])
            self.assertEqual(status['calculation'], 2)
            result = read_runtime_state(data.observation_dir(live)/'result.json')
            self.assertEqual(result['input_state'], changed)
            self.assertNotIn('sigma', result['input_state']['control']['run_setup'])

    def test_failed_calculation_records_error_without_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            live = root/'runtime.json'
            write_runtime_state(live, state())
            runtime = SimpleNamespace(profile=object(), vm=SimpleNamespace(runtime_json=live,
                bootstrap_lattice=root/'initial.lte', bootstrap_ele=root/'initial.ele', line_name='ALL'))
            def fail(*args):
                start_VM._stop_requested = True
                raise RuntimeError('simulation failure')
            with (patch.object(start_VM, 'resolve_machine_runtime', return_value=runtime),
                  patch.object(start_VM, 'ElegantParser'), patch.object(start_VM, 'VmPublisher'),
                  patch.object(start_VM, 'build_vm_publish_plan', return_value=SimpleNamespace(watch_scalar_specs=())),
                  patch.object(start_VM, 'describe_runtime_usedline', return_value='ALL'),
                  patch.object(start_VM, '_update_vm_outputs', side_effect=fail),
                  patch.object(start_VM.signal, 'signal'), patch.object(start_VM.time, 'sleep')):
                try:
                    start_VM.main()
                finally:
                    start_VM._stop_requested = False
            status = read_runtime_state(data.observation_dir(live)/'status.json')
            self.assertEqual(status['error'], 'simulation failure')
            self.assertIsNone(status['result'])

    def test_error_validation_does_not_partially_mutate(self):
        with tempfile.TemporaryDirectory() as temp:
            live = Path(temp)/'runtime.json'
            write_runtime_state(live, state())
            runtime = SimpleNamespace(vm=SimpleNamespace(runtime_json=live))
            with patch.object(err_gene_VM, 'resolve_machine_runtime', return_value=runtime):
                self.assertEqual(err_gene_VM.main(['err', 'gene_err', '3', '-1']), 1)
                self.assertEqual(read_runtime_state(live), state())
                self.assertEqual(err_gene_VM.main(['err', 'gene_err', '0', '2']), 0)
                output = read_runtime_state(live)
                self.assertEqual(output['lattice']['Q']['DX'], '0')
                self.assertEqual(output['control']['error_element']['amplitude'], '2e-06')


if __name__ == '__main__':
    unittest.main()
