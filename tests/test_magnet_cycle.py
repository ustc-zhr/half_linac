import threading
import unittest
from unittest.mock import patch

from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.apps.magnet_cycle.model import Cycle, Parameters
from half_linac.src.apps.magnet_cycle.profile_runtime import Magnet, group_magnets, load_magnets
from half_linac.src.apps.magnet_cycle.runner import Simulator, Stopped, run_cycles
from half_linac.src.shared.machine_profile import load_app_context


class CycleTests(unittest.TestCase):
    def parameters(self, **kwargs):
        return Parameters(**dict(dict(cycles=2, rate=2, hold=.02, tolerance=.01, stable=.02, timeout=1, period=.1), **kwargs))

    def test_full_bipolar_cycle_and_restore(self):
        cycle = Cycle(-1, 1, .3, .3, self.parameters(), 0)
        visited = []
        previous = cycle.command
        for i in range(1, 500):
            index = cycle.index
            value = cycle.tick(i * .1, cycle.command, cycle.command)
            if value is not None:
                self.assertLessEqual(abs(value - previous), .20000001)
                self.assertTrue(-1 <= value <= 1)
                previous = value
            if cycle.index != index:
                visited.append(cycle.targets[index])
            if cycle.done:
                break
        self.assertTrue(cycle.done)
        self.assertEqual(visited, [-1, 1, -1, 1, -1, .3])
        self.assertAlmostEqual(cycle.command, .3)

    def test_stall_holds_and_times_out(self):
        cycle = Cycle(0, 2, 1, 1, self.parameters(), 0)
        cycle.tick(.1, 1, 1)
        command = cycle.command
        self.assertIsNone(cycle.tick(.2, command, 1))
        self.assertEqual(cycle.command, command)
        with self.assertRaises(TimeoutError):
            cycle.tick(1.3, command, 1)

    def test_delayed_tick_does_not_jump(self):
        cycle = Cycle(0, 10, 5, 5, self.parameters(), 0)
        self.assertAlmostEqual(cycle.tick(100, 5, 5), 4.8)

    def test_hold_restarts_on_readback_excursion(self):
        cycle = Cycle(0, 1, 0, 0, self.parameters(stable=.2, hold=.2), 0)
        cycle.tick(.1, 0, 0)
        cycle.tick(.3, 0, .1)
        cycle.tick(.4, 0, 0)
        cycle.tick(.6, 0, 0)
        self.assertEqual(cycle.index, 0)
        cycle.tick(.81, 0, 0)
        self.assertEqual(cycle.index, 1)

    def test_invalid_initial_external_change_and_nan(self):
        with self.assertRaises(ValueError):
            Cycle(0, 1, 2, 2, self.parameters(), 0)
        with self.assertRaises(ValueError):
            Cycle(0, 1, .5, 0, self.parameters(), 0)
        for sp, rb, error in [(float('nan'), .5, ValueError), (.4, .5, RuntimeError)]:
            c = Cycle(0, 1, .5, .5, self.parameters(), 0)
            with self.assertRaises(error):
                c.tick(.1, sp, rb)

    def test_group_duplicates_and_reject_conflicts(self):
        a = Magnet('a', 'bend', -1, 1, 'set', 'read')
        b = Magnet('b', 'bend', -1, 1, 'set', 'read')
        self.assertEqual(len(group_magnets([a, b])), 1)
        with self.assertRaises(ValueError):
            group_magnets([a, Magnet('b', 'bend', 0, 1, 'set', 'read')])

    def test_stop_no_more_writes(self):
        m = Magnet('a', 'bend', -1, 1, 'set', 'read')
        io = Simulator([m])
        stop = threading.Event()
        writes = []
        def write(magnet, value):
            writes.append(value)
            stop.set()
        io.write = write
        with self.assertRaises(Stopped):
            run_cycles([m], self.parameters(), io, stop, lambda *args: None)
        self.assertEqual(len(writes), 1)

    def test_validate_entire_group_before_writing(self):
        magnets = [Magnet(n, 'bend', 0, 1, n, n + '_read') for n in ('a', 'b')]
        io = Simulator(magnets)
        io.values['b'] = 2
        with patch.object(io, 'write') as write:
            with self.assertRaises(ValueError):
                run_cycles(magnets, self.parameters(), io, threading.Event(), lambda *args: None)
            write.assert_not_called()

    def test_write_failure_aborts_other_magnets(self):
        magnets = [Magnet(n, 'bend', -1, 1, n, n + '_read') for n in ('a', 'b')]
        io = Simulator(magnets)
        with patch.object(io, 'write', side_effect=RuntimeError('failed')) as write:
            with self.assertRaisesRegex(RuntimeError, 'a: failed'):
                run_cycles(magnets, self.parameters(), io, threading.Event(), lambda *args: None)
            self.assertEqual(write.call_count, 1)

    def test_profile_simulation_and_missing_limits(self):
        context = load_app_context('magnet_cycle', machine_id='half', control_backend='vm')
        magnets = load_magnets(context, simulate=True)
        self.assertEqual(len([m for m in magnets if not m.error]), 178)
        bl = next(m for m in magnets if m.name == 'BL01A')
        self.assertEqual((bl.low, bl.high), (-100, 100))
        context = load_app_context('magnet_cycle', machine_id='irfel', control_backend='real')
        magnets = load_magnets(context)
        self.assertTrue(next(m for m in magnets if m.name == 'BM01').error)


if __name__ == '__main__':
    unittest.main()
