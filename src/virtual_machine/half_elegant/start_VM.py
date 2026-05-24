# Author: Biaobin Li
# Date: 2024-01-25
# 2024-08-29 changed by Shancai Zhang: run elegant when json file changed

import signal
import subprocess
import sys
import time
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.shared.machine_profile import resolve_machine_runtime
from half_linac.src.shared.runtime_state import ensure_runtime_state
from half_linac.src.virtual_machine.half_elegant.elegant_parser import elegant_parser


JSON_POLL_INTERVAL_S = 2.0

_stop_requested = False


def _handle_shutdown_signal(signum, frame):
    global _stop_requested
    _stop_requested = True


def _run_elegant(elegant_dir):
    subprocess.run(
        ["./one"],
        cwd=str(elegant_dir),
        check=True,
    )


def _update_vm_outputs(lte, elegant_dir, jsonpath):
    lattice_file = elegant_dir / "lattice.lte"
    ele_file = elegant_dir / "one.ele"

    lte.json2lte_ele(
        lat_f=str(lattice_file),
        ele_f=str(ele_file),
        j_file=str(jsonpath),
    )

    print("Elegant is running ...")
    _run_elegant(elegant_dir)
    if lte.broadcast_bpm():
        print("bpm data updated.")
    else:
        print("bpm publish skipped or incomplete.")

    if lte.broadcast_flag():
        print("flag data updated.")
    else:
        print("flag publish skipped or incomplete.")


def main():
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    runtime = resolve_machine_runtime()
    vm_dir = runtime.vm.root
    elegant_dir = runtime.vm.bootstrap_lattice.parent
    lattice_file = runtime.vm.bootstrap_lattice
    ele_file = runtime.vm.bootstrap_ele
    jsonpath = runtime.vm.runtime_json

    def build_initial_state():
        return elegant_parser(
            str(lattice_file),
            str(ele_file),
            runtime.vm.line_name,
        ).build_runtime_state()

    if ensure_runtime_state(jsonpath, build_initial_state):
        print("Initialized runtime lattice JSON.")
    else:
        print("Using existing runtime lattice JSON.")

    lte = elegant_parser(str(lattice_file), str(ele_file), runtime.vm.line_name)
    last_modified = jsonpath.stat().st_mtime

    _update_vm_outputs(lte, elegant_dir, jsonpath)
    print("VM is waiting for lattice changes.")

    while not _stop_requested:
        time.sleep(JSON_POLL_INTERVAL_S)
        current_modified = jsonpath.stat().st_mtime
        if current_modified == last_modified:
            continue

        last_modified = current_modified
        print("\njson changed, refreshing VM ...")
        try:
            _update_vm_outputs(lte, elegant_dir, jsonpath)
            print("VM is waiting for lattice changes.")
        except Exception as exc:
            print(f"failed to refresh VM after json change: {exc}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
