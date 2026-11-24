"""Private GUI worker: isolate the process tree, then reuse the existing CLI."""
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    job_dir = Path(sys.argv[1]).resolve()
    if os.name != "posix":
        raise RuntimeError("The digital shadow GUI worker requires a POSIX process group")
    if os.getpgrp() != os.getpid():
        os.setsid()
    (job_dir / "worker.pgid").write_text(str(os.getpgrp()))
    arguments = sys.argv[2:]
    if not arguments or arguments[0] not in {"replay", "run-vm", "demo-real", "run-real", "run-model",
                                              "capture-particle-snapshot", "follow-particle"}:
        raise ValueError("Unsupported shadow worker mode")
    if arguments[0] == "replay":
        snapshot_path = Path(arguments[arguments.index("--snapshot") + 1])
        snapshot = json.loads(snapshot_path.read_text())
        machine = snapshot["machine"]
        if machine["id"] != "half" or machine["control_backend"] not in {"vm", "real"} or (
            machine["control_backend"] == "real" and "--real-evidence" not in arguments
        ):
            raise ValueError("Real or non-HALF snapshots are view-only in this GUI")
    elif arguments[0] in {"run-vm", "demo-real", "run-real"} and arguments[arguments.index("--machine") + 1] != "half":
        raise ValueError("This GUI supports HALF only")
    if arguments[0] == "run-real" and "--connect-real" not in arguments:
        raise ValueError("Real reads require explicit --connect-real")
    root = next(parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file())
    cli = runpy.run_path(str(root / "scripts/run_digital_shadow.py"))
    return cli["main"](arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, IndexError, RuntimeError, OSError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
