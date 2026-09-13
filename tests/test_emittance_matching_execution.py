"""K1 execution tests with in-memory PVs only."""
from copy import deepcopy
import unittest

from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.apps.emit_measure.matching_execution import K1Execution


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.pvs = {"Q1": 1., "Q2": 2., "FIXED": 3.}
        self.history = []
        self.writes = []
        self.cancel = False
        self.targets = {q: {"pv": q, "current": v, "suggested": v + .1}
                        for q, v in list(self.pvs.items())[:2]}
        self.record = {}

    def write(self, pv, value):
        self.assertTrue(self.history[-1]["original"])
        self.assertIn(pv, self.history[-1]["attempted"])
        self.writes.append((pv, value))
        self.pvs[pv] = value
        return 1

    def executor(self, write=None, allowed=lambda: None):
        return K1Execution(self.pvs.get, write or self.write,
            lambda r: self.history.append(deepcopy(r)), allowed,
            lambda: self.cancel, timeout=0., poll=0.)

    def test_apply_then_restore(self):
        io = self.executor()
        io.apply(self.record, self.targets, dict(self.pvs))
        self.assertEqual(self.record["status"], "applied")
        self.assertEqual(self.pvs["Q1"], 1.1)
        io.restore(self.record)
        self.assertEqual(self.record["status"], "restored")
        self.assertEqual(self.pvs, {"Q1": 1., "Q2": 2., "FIXED": 3.})
        self.assertEqual([p for p, v in self.writes], ["Q1", "Q2", "Q2", "Q1"])

    def test_stale_nonadjustable_baseline_blocks_all_writes(self):
        expected = dict(self.pvs)
        self.pvs["FIXED"] = 4
        self.executor().apply(self.record, self.targets, expected)
        self.assertEqual(self.record["status"], "apply_failed")
        self.assertFalse(self.writes)

    def test_failed_write_may_have_changed_device_and_is_restored(self):
        def uncertain_write(pv, value):
            self.write(pv, value)
            return None if pv == "Q2" else 1
        self.executor(uncertain_write).apply(self.record, self.targets, dict(self.pvs))
        self.assertEqual(self.record["attempted"], ["Q1", "Q2"])
        self.assertEqual(self.record["verified"], ["Q1"])
        self.assertEqual(self.record["status"], "apply_failed")
        self.executor().restore(self.record)
        self.assertEqual(self.pvs["Q2"], 2.)

    def test_cancel_keeps_originals(self):
        def cancel_after_write(pv, value):
            self.write(pv, value)
            self.cancel = True
            return 1
        self.executor(cancel_after_write).apply(self.record, self.targets, dict(self.pvs))
        self.assertEqual(self.record["attempted"], ["Q1"])
        self.cancel = False
        self.executor().restore(self.record)
        self.assertEqual(self.pvs["Q1"], 1.)

    def test_readback_timeout_and_restore_retry(self):
        self.executor(lambda pv, v: 1).apply(self.record, self.targets, dict(self.pvs))
        self.assertEqual(self.record["status"], "apply_failed")
        self.pvs["Q1"] = 8.
        self.executor(lambda pv, v: 1).restore(self.record)
        self.assertEqual(self.record["status"], "restore_failed")
        self.executor().restore(self.record)
        self.assertEqual(self.record["status"], "restored")

    def test_permission_and_journal_failure_block_writes(self):
        def denied():
            raise ValueError("Write policy denies this backend")
        self.executor(allowed=denied).apply(self.record, self.targets, dict(self.pvs))
        self.assertFalse(self.writes)
        def failed_save(record):
            raise OSError("Disk unavailable")
        io = self.executor()
        io.persist = failed_save
        with self.assertRaises(OSError):
            io.apply({}, self.targets, dict(self.pvs))
        self.assertFalse(self.writes)


if __name__ == "__main__":
    unittest.main()
