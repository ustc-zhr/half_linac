from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timezone

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
    reconcile_watch_scalar_sources,
)
from half_linac.src.shared.machine_profile import resolve_machine_runtime
from half_linac.src.shared.runtime_state import (
    ensure_runtime_state,
    read_runtime_state,
    update_runtime_state,
)
from half_linac.src.virtual_machine.lattice_usedline import describe_runtime_usedline
from half_linac.src.virtual_machine.workbench_data import (
    input_version, observation_dir, collect_results, save_observation,
)


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


def _format_device_count(count):
    return f"{count} device{'s' if count != 1 else ''}"


def _update_vm_outputs(parser, publisher, publish_plan, elegant_dir, jsonpath):
    lattice_file = elegant_dir / "lattice.lte"
    ele_file = elegant_dir / "one.ele"

    parser.json_to_lte_ele(lattice_file, ele_file, jsonpath)

    print("Running Elegant simulation...")
    _run_elegant(elegant_dir)
    print("Elegant simulation completed.")

    usedline = read_runtime_state(jsonpath)["usedline"]
    publication = {}
    def publish(function, *args, **kwargs):
        try:
            return bool(function(*args, **kwargs))
        except Exception as exc:
            print(f"Diagnostic publication failed: {exc}", file=sys.stderr)
            return False

    bpm_count = len(publish_plan.bpm_specs)
    if bpm_count:
        publication["bpm"] = publish(publisher.publish_bpms, publish_plan, elegant_dir / "one.bpmcen")
        if publication["bpm"]:
            print(f"Published BPM positions ({_format_device_count(bpm_count)}).")
        else:
            print(
                "BPM position publishing skipped or incomplete "
                f"({_format_device_count(bpm_count)})."
            )

    image_count = len(publish_plan.watch_image_specs)
    if image_count:
        publication["screens"] = publish(publisher.publish_watch_images,
            publish_plan,
            lattice=parser.lattice,
            usedline=usedline,
            elegant_dir=elegant_dir,
        )
        if publication["screens"]:
            print(f"Published screen images ({_format_device_count(image_count)}).")
        else:
            print(
                "Screen image publishing skipped or incomplete "
                f"({_format_device_count(image_count)})."
            )

    scalar_count = len(publish_plan.watch_scalar_specs)
    if scalar_count:
        publication["charge"] = publish(publisher.publish_watch_scalars,
            publish_plan,
            lattice=parser.lattice,
            usedline=usedline,
            elegant_dir=elegant_dir,
        )
        if publication["charge"]:
            print(f"Published ICT bunch charge ({_format_device_count(scalar_count)}).")
        else:
            print(
                "ICT bunch charge publishing skipped or incomplete "
                f"({_format_device_count(scalar_count)})."
            )

    return publication

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
    print(f"Current VM usedline: {describe_runtime_usedline(runtime)}")

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
    if publish_plan.watch_scalar_specs:
        _, diagnostics_changed = update_runtime_state(
            jsonpath,
            lambda state: reconcile_watch_scalar_sources(
                state,
                parser.lattice,
                publish_plan.watch_scalar_specs,
            ),
        )
        if diagnostics_changed:
            print("Synchronized scalar diagnostic WATCH elements into VM runtime state.")
    session = os.environ.get("HALF_VM_SESSION") or uuid.uuid4().hex
    directory = observation_dir(jsonpath)
    status = dict(session=session, pid=os.getpid(), calculation=0, phase="Starting",
                  result=None, error=None, publication={})
    last_version = None
    save_observation(directory / "status.json", status)
    while not _stop_requested:
        try:
            state = read_runtime_state(jsonpath)
            version = input_version(state)
            if version == last_version:
                time.sleep(JSON_POLL_INTERVAL_S)
                continue
            last_version = version
            status.update(calculation=status["calculation"] + 1, phase="Calculating",
                          input_version=version, error=None, publication={})
            save_observation(directory / "status.json", status)
            started = time.monotonic()
            output_start = time.time()
            # The live JSON remains the only input watched by this loop.
            # Enable statistical output only in the frozen simulation input.
            original_input = copy.deepcopy(state)
            state["control"]["run_setup"]["sigma"] = "%s.sig"
            frozen = directory / "input.json"
            save_observation(frozen, state)
            parser.lattice = state["lattice"]
            publication = _update_vm_outputs(parser, publisher, publish_plan, elegant_dir, frozen)
            result = collect_results(state, elegant_dir, newer_than=output_start)
            result["input_state"] = original_input
            result.update(session=session, calculation=status["calculation"], input_version=version)
            # Atomic replacement keeps readers isolated from Elegant's output writes.
            save_observation(directory / "result.json", result)
            status.update(phase="Ready", publication=publication, result="result.json",
                          result_version=version, completed_at=datetime.now(timezone.utc).isoformat(),
                          elapsed=time.monotonic() - started)
            print("VM ready; waiting for lattice changes...", flush=True)
        except Exception as exc:
            status.update(phase="Failed", error=str(exc))
            print(f"VM calculation failed: {exc}", file=sys.stderr, flush=True)
            time.sleep(JSON_POLL_INTERVAL_S)
        save_observation(directory / "status.json", status)
    status["phase"] = "Stopped"
    save_observation(directory / "status.json", status)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
