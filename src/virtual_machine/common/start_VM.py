from __future__ import annotations

import signal
import shutil
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

from half_linac.src.shared.elegant_backend import (
    ElegantParser,
    VmPublisher,
    build_vm_publish_plan,
)
from half_linac.src.shared.machine_profile import resolve_machine_runtime
from half_linac.src.shared.runtime_state import ensure_runtime_state, read_runtime_state


JSON_POLL_INTERVAL_S = 2.0
MISSING_LIBRARY_MARKER = "error while loading shared libraries:"

_stop_requested = False


def _handle_shutdown_signal(signum, frame):
    global _stop_requested
    _stop_requested = True


def _run_elegant(elegant_dir):
    try:
        result = subprocess.run(
            ["./one"],
            cwd=str(elegant_dir),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"VM elegant runner is missing: {elegant_dir / 'one'}") from exc

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode == 0:
        return

    elegant_path = shutil.which("elegant") or "elegant"
    message = f"elegant failed with exit code {result.returncode}."
    if MISSING_LIBRARY_MARKER in result.stderr:
        missing_detail = result.stderr.strip().split(MISSING_LIBRARY_MARKER, 1)[1].strip()
        message = (
            f"{message} Missing runtime library for {elegant_path}: {missing_detail}\n"
            "Install the matching GSL runtime library or rebuild elegant against the GSL "
            "version available in this environment."
        )
    raise RuntimeError(message)


def _update_vm_outputs(parser, publisher, publish_plan, elegant_dir, jsonpath):
    lattice_file = elegant_dir / "lattice.lte"
    ele_file = elegant_dir / "one.ele"

    parser.json_to_lte_ele(lattice_file, ele_file, jsonpath)

    print("Elegant is running ...")
    _run_elegant(elegant_dir)
    if publisher.publish_bpms(publish_plan, elegant_dir / "one.bpmcen"):
        print("bpm data updated.")
    else:
        print("bpm publish skipped or incomplete.")

    if publisher.publish_watch_images(
        publish_plan,
        lattice=parser.lattice,
        usedline=read_runtime_state(jsonpath)["usedline"],
        elegant_dir=elegant_dir,
    ):
        print("flag data updated.")
    else:
        print("flag publish skipped or incomplete.")


def main():
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    runtime = resolve_machine_runtime()
    elegant_dir = runtime.vm.bootstrap_lattice.parent
    lattice_file = runtime.vm.bootstrap_lattice
    ele_file = runtime.vm.bootstrap_ele
    jsonpath = runtime.vm.runtime_json

    def build_initial_state():
        return ElegantParser(
            lattice_file,
            ele_file,
            runtime.vm.line_name,
            runtime_json_path=jsonpath,
            elegant_dir=elegant_dir,
        ).build_runtime_state()

    if ensure_runtime_state(jsonpath, build_initial_state):
        print("Initialized runtime lattice JSON.")
    else:
        print("Using existing runtime lattice JSON.")

    parser = ElegantParser(
        lattice_file,
        ele_file,
        runtime.vm.line_name,
        runtime_json_path=jsonpath,
        elegant_dir=elegant_dir,
    )
    try:
        publish_plan = build_vm_publish_plan(runtime.profile)
        publisher = VmPublisher()
    except Exception as exc:
        print(f"failed to build VM publish plan: {exc}")
        return 1
    last_modified = jsonpath.stat().st_mtime

    try:
        _update_vm_outputs(parser, publisher, publish_plan, elegant_dir, jsonpath)
    except Exception as exc:
        print(f"failed to start VM runtime: {exc}", file=sys.stderr)
        return 1
    print("VM is waiting for lattice changes.")

    while not _stop_requested:
        time.sleep(JSON_POLL_INTERVAL_S)
        current_modified = jsonpath.stat().st_mtime
        if current_modified == last_modified:
            continue

        last_modified = current_modified
        print("\njson changed, refreshing VM ...")
        try:
            _update_vm_outputs(parser, publisher, publish_plan, elegant_dir, jsonpath)
            print("VM is waiting for lattice changes.")
        except Exception as exc:
            print(f"failed to refresh VM after json change: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
