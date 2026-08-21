from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.machine_profile.control_lock import (
    ControlLockError,
    EnergyControlLock,
)


class EnergyControlLockTests(unittest.TestCase):
    def test_exclusive(self):
        import tempfile
        from pathlib import Path
        tmp_path = Path(tempfile.mkdtemp())
        first = EnergyControlLock(tmp_path / "energy.lock", {"app": "first"})
        second = EnergyControlLock(tmp_path / "energy.lock", {"app": "second"})
        first.acquire()
        try:
            with self.assertRaises(ControlLockError):
                second.acquire()
            owner = json.loads((tmp_path / "energy.lock").read_text())
            assert owner["app"] == "first"
        finally:
            first.release()
        second.acquire()
        second.release()


    def test_context_release(self):
        import tempfile
        from pathlib import Path
        tmp_path = Path(tempfile.mkdtemp())
        path = tmp_path / "energy.lock"
        with EnergyControlLock(path, {"operation": "scan"}):
            assert path.exists()
        other = EnergyControlLock(path, {"operation": "next"})
        other.acquire()
        other.release()
