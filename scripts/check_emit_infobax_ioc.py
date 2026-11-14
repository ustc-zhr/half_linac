#!/usr/bin/env python3
"""Bounded InfoBAX/elegant acceptance on a private loopback-only soft IOC."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid


def private_environment(port, repeater_port):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("EPICS_CA", "EPICS_CAS"))}
    environment.update(
        EPICS_CA_AUTO_ADDR_LIST="NO", EPICS_CA_ADDR_LIST=f"127.0.0.1:{port}",
        EPICS_CA_SERVER_PORT=str(port), EPICS_CAS_SERVER_PORT=str(port),
        EPICS_CA_REPEATER_PORT=str(repeater_port), EPICS_CAS_BEACON_PORT=str(repeater_port),
        EPICS_CAS_INTF_ADDR_LIST="127.0.0.1", EPICS_CAS_AUTO_BEACON_ADDR_LIST="NO",
        EPICS_CAS_BEACON_ADDR_LIST=f"127.0.0.1:{repeater_port}",
        EPICS_CA_MAX_ARRAY_BYTES="10000000",
    )
    return environment


def unused_port():
    with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        tcp.bind(("127.0.0.1", 0))
        port = tcp.getsockname()[1]
        udp.bind(("127.0.0.1", port))
        return port


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def isolated_profile(profile, prefix):
    elements = tuple(replace(element, channels={
        name: {"vm": f"{prefix}{element.id}:{name}"} if "vm" in modes else {}
        for name, modes in element.channels.items()
    }) for element in profile.elements)
    return replace(profile, elements=elements, _elements_by_id={value.id: value for value in elements})


def imports():
    # The launcher sets the CA environment in a new process before any pyepics
    # import. Direct worker invocation with an ambient machine CA setup fails.
    port = os.environ.get("EPICS_CA_SERVER_PORT", "")
    if (os.environ.get("EPICS_CA_ADDR_LIST") != f"127.0.0.1:{port}"
            or os.environ.get("EPICS_CA_AUTO_ADDR_LIST") != "NO"
            or os.environ.get("EPICS_CAS_INTF_ADDR_LIST") != "127.0.0.1"):
        raise RuntimeError("worker requires an isolated loopback CA environment")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from repo_bootstrap import ensure_repo_import_path

    ensure_repo_import_path(__file__)


def vm_worker(root, prefix):
    imports()
    from half_linac.src.shared.machine_profile import load_profile, resolve_machine_runtime
    from half_linac.src.virtual_machine.common import start_VM

    profile = isolated_profile(load_profile("half"), prefix)
    runtime = resolve_machine_runtime(profile)
    runtime = replace(runtime, vm=replace(
        runtime.vm, root=root, bootstrap_lattice=root / "source.lte",
        bootstrap_ele=root / "source.ele", runtime_json=root / "machine.json", line_name="SMOKE",
    ))
    original_plan = start_VM.build_vm_publish_plan
    plan = original_plan(profile)
    plan = replace(plan, bpm_specs=(), watch_scalar_specs=(), watch_image_specs=tuple(
        spec for spec in plan.watch_image_specs
        if spec.target_element_id == "PRF07" and spec.logical_channel == "image"
    ))
    start_VM.resolve_machine_runtime = lambda: runtime
    start_VM.build_vm_publish_plan = lambda _profile: plan
    start_VM.JSON_POLL_INTERVAL_S = 0.05
    return start_VM.main()


def validate_measurement_options(particles, noise_scale, method):
    if type(particles) is not int or particles < 1000:
        raise ValueError("particle count must be an integer of at least 1000")
    if not math.isfinite(noise_scale) or noise_scale <= 0:
        raise ValueError("noise scale must be positive and finite")
    if method != "rms_count_variance" and noise_scale != 1.0:
        raise ValueError("a fitted noise scale requires rms_count_variance")


def acceptance(output, prefix, measurement_method="gaussian_fit", *, particles=10000, noise_scale=1.0):
    validate_measurement_options(particles, noise_scale, measurement_method)
    imports()
    import epics
    from check_emit_infobax_vm import prepare_workspace
    from half_linac.src.apps.emit_measure.infobax_session import (
        InfoBAXSession, InfoBAXSessionConfig, SessionRunFailure, SessionCompatibilityError,
    )
    from half_linac.src.apps.emit_measure.infobax_measurement import RmsVarianceConfig, measure_count_variance
    from half_linac.src.apps.emit_measure.infobax_vm import (
        FiniteWorkpointTable, HalfVmInfoBAXMachine, MachineVariable, build_half_vm_infobax_pool,
    )
    from half_linac.src.optimization.infobax import InfoBAXCore, InfoBAXConfig
    from half_linac.src.shared.elegant_backend import ElegantParser
    from half_linac.src.shared.machine_profile import resolve_channel, resolve_write_target, resolve_element_image_geometry
    from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state
    from half_linac.src.softIOC.pv_server import pv_server
    from half_linac.src.virtual_machine.workbench_data import input_version, observation_dir
    import shutil

    root = output / "workspace"
    root.mkdir()
    context = prepare_workspace(root, particles=particles)
    context = replace(context, profile=isolated_profile(context.profile, prefix))
    parser = ElegantParser(root / "source.lte", root / "source.ele", "SMOKE")
    runtime_json = root / "machine.json"
    write_runtime_state(runtime_json, parser.build_runtime_state())
    targets = tuple(resolve_write_target(context, element, quantity="K1", unit="1/m^2", mode="vm")
                    for element in ("QT01", "QT02"))
    image_pv = resolve_channel(context, "PRF07", "image", "vm")
    geometry = resolve_element_image_geometry(context, "PRF07", "vm")
    rms_config = RmsVarianceConfig(noise_scale=noise_scale) if measurement_method == "rms_count_variance" else None
    database = "\n".join(
        f'record(ao, "{target.pv_name}") {{ field(VAL, "{value}") }}'
        for target, value in zip(targets, (0.1, -0.2))
    ) + f'\nrecord(waveform, "{image_pv}") {{ field(FTVL, "DOUBLE") field(NELM, "388800") }}\n'
    (root / "smoke.db").write_text(database)
    table = FiniteWorkpointTable(("low", "center", "high"), (MachineVariable("QT01"),), [[-1], [0], [1]])
    pool = build_half_vm_infobax_pool(
        context, table, scan_quad="QT02", screen="PRF07", plane="x", model_line="SMOKE",
        scan_values=(-1.0, 0.0, 1.0), energy_mev=225, observation_noise_std_mm2=0.002,
        measurement_method=measurement_method, rms_config=rms_config,
    )
    with ExitStack() as stack:
        ioc_log = stack.enter_context((output / "ioc.log").open("w"))
        ioc = subprocess.Popen([shutil.which("softIoc") or "softIoc", "-S", "-d", str(root / "smoke.db")],
                               cwd=root, stdout=ioc_log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        stack.callback(stop, ioc)
        deadline = time.monotonic() + 20
        while epics.caget(targets[0].pv_name, timeout=0.2, use_monitor=False) is None:
            if ioc.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("private IOC did not start; see ioc.log")
            time.sleep(0.05)
        # Use the actual IOC->JSON monitor; no direct setpoint mirroring in the test driver.
        server = pv_server(runtime_json, root, machine_id="half")
        server._pv_to_lattice_field = {target.pv_name: (target.element_id, "K1") for target in targets}
        server.pvl = [target.pv_name for target in targets]
        server.monitor_json()
        stack.callback(lambda: [pv.clear_callbacks() for pv in server.pv_objects])
        for pv in server.pv_objects:
            if not pv.wait_for_connection(timeout=5):
                raise RuntimeError("IOC monitor did not connect")
        vm_log = stack.enter_context((output / "vm.log").open("w"))
        vm = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--vm-worker",
                               "--root", str(root), "--prefix", prefix],
                              cwd=root, stdout=vm_log, stderr=subprocess.STDOUT)
        stack.callback(stop, vm)
        machine = HalfVmInfoBAXMachine(context, table, scan_quad="QT02", screen="PRF07", plane="x",
                                      runtime_json=runtime_json, measurement_timeout=30,
                                      measurement_method=measurement_method, rms_config=rms_config)
        initial = machine.snapshot()
        holder = {}
        frame_audits = []

        def progress(payload):
            record = payload["record"]
            if record is None or record["state"] != "observed":
                return
            if measurement_method == "rms_count_variance":
                measured = record["measurement"]
                raw = epics.caget(image_pv, timeout=5, use_monitor=False)
                unit = measure_count_variance(raw, pixel_shape=geometry.shape, pixel_width_mm=geometry.pixel_width_mm)
                if (unit.count != particles or measured["metadata"]["count"] != particles
                        or measured["metadata"]["noise_scale"] != noise_scale
                        or not math.isclose(measured["variance_mm2"], unit.variance_mm2, rel_tol=1e-12)
                        or not math.isclose(measured["noise_std_mm2"], unit.noise_std_mm2*noise_scale, rel_tol=1e-12)):
                    raise RuntimeError("live IOC image count or noise-scale arithmetic does not match the committed frame")
                frame_audits.append(dict(query_index=record["query_index"], count=unit.count,
                                        variance_mm2=unit.variance_mm2, unscaled_noise_std_mm2=unit.noise_std_mm2,
                                        scaled_noise_std_mm2=measured["noise_std_mm2"], noise_scale=noise_scale))
            if payload["observation_count"] == 3:
                holder["session"].request_pause()

        session = InfoBAXSession(InfoBAXCore(pool, InfoBAXConfig(seed=0)), machine,
                                output / "session", InfoBAXSessionConfig(max_observations=7), progress=progress)
        holder["session"] = session
        session.run()
        if session.status != "paused" or machine.snapshot() != initial:
            raise RuntimeError("pause did not restore the IOC setpoints")
        resumed = InfoBAXSession.from_run_dir(pool, machine, output / "session", progress=progress)
        holder["session"] = resumed
        resumed.run()
        if resumed.status != "completed" or len(resumed.core.observations) != 7 or machine.snapshot() != initial:
            raise RuntimeError("IOC resume/completion/restoration failed")
        incompatible_scale_rejected = None
        if measurement_method == "rms_count_variance":
            if len(frame_audits) != 7:
                raise RuntimeError("not every resumed IOC frame was audited")
            wrong_scale = 1.0 if noise_scale != 1.0 else 0.5
            incompatible = HalfVmInfoBAXMachine(
                context, table, scan_quad="QT02", screen="PRF07", plane="x", runtime_json=runtime_json,
                measurement_timeout=30,
                measurement_method=measurement_method, rms_config=RmsVarianceConfig(noise_scale=wrong_scale))
            try:
                InfoBAXSession.from_run_dir(pool, incompatible, output / "session")
            except SessionCompatibilityError:
                incompatible_scale_rejected = True
            else:
                raise RuntimeError("checkpoint with an incompatible noise scale was accepted")
        # Freeze only our own VM, leave old image and Ready status in place, then
        # prove that an actual CA setpoint change cannot commit the stale frame.
        os.kill(vm.pid, signal.SIGSTOP)
        stack.callback(lambda: os.kill(vm.pid, signal.SIGCONT) if vm.poll() is None else None)
        time.sleep(0.1)
        stale_machine = HalfVmInfoBAXMachine(
            context, table, scan_quad="QT02", screen="PRF07", plane="x",
            runtime_json=runtime_json, measurement_timeout=0.5,
            measurement_method=measurement_method, rms_config=rms_config,
        )
        stale = InfoBAXSession(InfoBAXCore(pool), stale_machine, output / "stale-session",
                              InfoBAXSessionConfig(max_observations=6))
        try:
            stale.run()
        except SessionRunFailure:
            pass
        else:
            raise RuntimeError("stale frame was accepted")
        if (stale.core.observations or stale.restoration_status != "succeeded"
                or machine.snapshot() != initial or "timeout" not in (stale.error or "")):
            raise RuntimeError("stale-frame failure did not preserve accounting and restoration")
        os.kill(vm.pid, signal.SIGCONT)
        # The IOC readback can precede monitor callbacks. Wait until the VM has
        # also calculated the restored input, not merely until caput returns.
        deadline = time.monotonic() + 30
        while True:
            state = read_runtime_state(runtime_json)
            status = read_runtime_state(observation_dir(runtime_json) / "status.json")
            if (status.get("phase") == "Ready" and status.get("result_version") == input_version(state)
                    and all(abs(float(state["lattice"][target.element_id]["K1"]) - initial[target.pv_name]) < 1e-6
                            for target in targets)):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("VM did not catch up to the restored IOC state")
            time.sleep(0.05)
    return dict(status="passed", observations=7, active_queries=1, paused_and_resumed=True,
                restored=True, stale_frame_rejected=True, stale_observations=0,
                ca_io="real pyepics/softIoc", vm="actual start_VM.main + elegant",
                ioc_to_json="actual pv_server.monitor_json", prefix=prefix,
                measurement_method=measurement_method,
                particles=particles, noise_scale=noise_scale, live_frame_audits=frame_audits,
                incompatible_scale_checkpoint_rejected=incompatible_scale_rejected,
                pool_fingerprint=pool.fingerprint,
                ca_address=os.environ["EPICS_CA_ADDR_LIST"], processes_cleaned_up=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--measurement-method", choices=("gaussian_fit", "rms_count_variance"), default="gaussian_fit")
    parser.add_argument("--particles", type=int, default=10000)
    parser.add_argument("--noise-scale", type=float, default=1.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--vm-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--prefix", help=argparse.SUPPRESS)
    args = parser.parse_args()
    validate_measurement_options(args.particles, args.noise_scale, args.measurement_method)
    if args.worker or args.vm_worker:
        if not args.prefix or not args.prefix.startswith("INFOBAX:TEST:"):
            raise ValueError("worker requires a private test PV prefix")
        if args.vm_worker:
            return vm_worker(args.root, args.prefix)
        result = acceptance(args.output_dir, args.prefix, args.measurement_method,
                            particles=args.particles, noise_scale=args.noise_scale)
        (args.output_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        return 0
    if args.output_dir is None:
        parser.error("--output-dir is required")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    port, repeater = unused_port(), unused_port()
    prefix = f"INFOBAX:TEST:{uuid.uuid4().hex[:12]}:"
    with (output / "driver.log").open("w") as log:
        worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker",
                                   "--output-dir", str(output), "--prefix", prefix,
                                   "--measurement-method", args.measurement_method,
                                   "--particles", str(args.particles), "--noise-scale", str(args.noise_scale)],
                                  env=private_environment(port, repeater), start_new_session=True,
                                  stdout=log, stderr=subprocess.STDOUT)
        try:
            status = worker.wait(timeout=180)
        finally:
            # Include any CA repeater spawned by libca, as well as direct children.
            try:
                os.killpg(worker.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            stop(worker)
    if status:
        raise RuntimeError(f"isolated IOC acceptance failed; see {output / 'driver.log'}")
    print((output / "summary.json").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
