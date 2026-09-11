"""Batch safety checks without EPICS or a GUI session."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "energy_buttons_control", Path(__file__).resolve().parents[1]
    / "src/apps/energy_buttons/control.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class EnergyBatchTests(unittest.TestCase):
    def test_stale_preflight_writes_nothing(self):
        backend = control.DemoBackend()
        a, b = control.PVS[:2]
        backend.values[b] = 401
        with self.assertRaises(ValueError):
            control.execute(backend, [(a, 250, 230), (b, 400, 380)], lambda *args: None)
        self.assertEqual(backend.read(a), 250)

    def test_failure_stops_remaining_writes(self):
        a, b, c = control.PVS[:3]

        class Failing(control.DemoBackend):
            def write(self, name, target):
                if name == b:
                    raise RuntimeError("injected failure")
                super().write(name, target)

        backend = Failing()
        with self.assertRaises(RuntimeError):
            control.execute(backend, [(a, 250, 230), (b, 400, 380), (c, 660, 640)], lambda *args: None)
        self.assertEqual(backend.read(a), 230)
        self.assertEqual(backend.read(c), 660)

    def test_invalid_target_prevents_whole_batch(self):
        for target in (0, -1, float("nan"), float("inf")):
            backend = control.DemoBackend()
            a, b = control.PVS[:2]
            with self.assertRaises(ValueError):
                control.execute(backend, [(a, 250, 230), (b, 400, target)], lambda *args: None)
            self.assertEqual(backend.read(a), 250)


if __name__ == "__main__":
    unittest.main()
