from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from half_linac.src.apps.solenoid_field_guide.current_control import (
    EpicsScalarIO,
    CurrentControl,
    VerificationConfig,
    _apply_with_io,
)
from half_linac.src.shared.machine_profile import LimitRange, WriteTarget


def main() -> int:
    if len(sys.argv) != 7:
        print("usage: epics_apply.py SETPOINT_PV READBACK_PV CURRENT TOLERANCE TIMEOUT POLL", file=sys.stderr)
        return 2
    setpoint_pv, readback_pv = sys.argv[1:3]
    current, tolerance, timeout, poll = (float(value) for value in sys.argv[3:])
    target = WriteTarget("solenoid", "solenoid", "real", "current_set", setpoint_pv, "A", LimitRange())
    result = _apply_with_io(
        CurrentControl(target, readback_pv),
        "solenoid",
        current,
        EpicsScalarIO(timeout_s=min(2.0, timeout)),
        VerificationConfig(tolerance, timeout, poll),
    )
    print(json.dumps({
        "status": result.status,
        "readback_current": result.readback_current,
        "message": result.message,
    }))
    return 0 if result.status in {"applied", "mismatch", "failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
