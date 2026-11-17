"""Opt-in acceptance with the actual headless VM worker and private CA services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("HALF_VM_SHADOW_INTEGRATION") != "1",
                    reason="Requires explicitly enabled private EPICS/Elegant acceptance")
def test_private_vm_worker_acceptance(tmp_path):
    completed = subprocess.run([sys.executable, "-B", __file__, "accept", str(tmp_path)],
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((tmp_path / "acceptance.json").read_text())
    assert report["status"] == "passed"
    assert len(report["working_points"]) == 4
    assert len(report["rejections"]) == 3


def _imports():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts/python_imports"))
    return root


def _worker(directory, line):
    """Redirect filesystem paths only; run the production worker loop unchanged."""
    _imports()
    from dataclasses import replace
    from unittest.mock import patch
    from half_linac.src.shared.machine_profile import resolve_machine_runtime
    from half_linac.src.virtual_machine.common import start_VM

    runtime = resolve_machine_runtime("half")
    vm = replace(runtime.vm, runtime_json=directory / "runtime.json",
                 bootstrap_lattice=directory / "lattice_ini.lte",
                 bootstrap_ele=directory / "one_ini.ele", line_name=line)
    with patch.object(start_VM, "resolve_machine_runtime", return_value=replace(runtime, vm=vm)):
        raise SystemExit(start_VM.main())


def _offline(arguments):
    root = _imports()
    import epics
    import runpy

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline acceptance must not read or write any PV")
    for name in ("PV", "caget", "caput", "caget_many", "caput_many"):
        setattr(epics, name, forbidden)
    cli = runpy.run_path(str(root / "scripts/run_digital_shadow.py"))
    raise SystemExit(cli["main"](arguments))


def _accept(directory):
    from dataclasses import replace
    import hashlib
    import math
    import socket
    import time

    # Discovery is isolated before importing EPICS, including all child processes.
    sockets = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2)]
    try:
        for udp in sockets:
            udp.bind(("127.0.0.1", 0))
        server_port, repeater_port = [udp.getsockname()[1] for udp in sockets]
    finally:
        for udp in sockets:
            udp.close()
    os.environ.update(EPICS_CA_AUTO_ADDR_LIST="NO", EPICS_CA_ADDR_LIST="127.0.0.1",
                      EPICS_CA_SERVER_PORT=str(server_port), EPICS_CAS_SERVER_PORT=str(server_port),
                      EPICS_CA_REPEATER_PORT=str(repeater_port), EPICS_CAS_INTF_ADDR_LIST="127.0.0.1",
                      EPICS_CAS_AUTO_BEACON_ADDR_LIST="NO", EPICS_CAS_BEACON_ADDR_LIST="127.0.0.1",
                      EPICS_CA_MAX_ARRAY_BYTES="20000000", HALF_LINAC_CONTROL_BACKEND="real")
    root = _imports()
    import epics
    import numpy as np
    from half_linac.src.shared.digital_shadow.models import Diagnostic, ShadowRequest
    from half_linac.src.shared.digital_shadow.pipeline import prepare_model
    from half_linac.src.shared.elegant_backend import build_vm_publish_plan
    from half_linac.src.shared.machine_profile.loader import load_model_context
    from half_linac.src.shared.machine_profile.resolver import resolve_channel
    from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state
    from half_linac.src.virtual_machine.workbench_data import columns, input_version, observation_dir

    context = load_model_context("half")
    plan = build_vm_publish_plan(context.profile)
    # Supply the existing publisher's complete PV plan on the private softIOC.
    scalar_names = {name for spec in plan.bpm_specs for name in (spec.x_pv, spec.y_pv)}
    scalar_names.update(spec.pv_name for spec in plan.watch_scalar_specs)
    scalar_names.update(name for spec in plan.watch_rms_specs for name in (spec.x_pv, spec.y_pv) if name)
    scalar_names.update(resolve_channel(context, element, "K1", "vm") for element in ("QT01", "QT02"))
    real_names = {mapping["real"] for element in context.profile.elements
                  for mapping in element.channels.values() if "real" in mapping}
    records = [f'record(ao, "{name}") {{ field(PREC,"12") }}' for name in sorted(scalar_names)]
    all_names = set(scalar_names)
    for spec in plan.watch_image_specs:
        all_names.add(spec.pv_name)
        records.append(f'record(waveform, "{spec.pv_name}") {{ field(FTVL,"DOUBLE") '
                       f'field(NELM,"{math.prod(spec.pixel_shape)}") }}')
    assert all_names.isdisjoint(real_names)
    (directory / "private.db").write_text("\n".join(records))

    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)

    def metadata(names):
        result = {}
        for name in names:
            pv = epics.PV(name, auto_monitor=False)
            try:
                assert pv.wait_for_connection(timeout=3), name
                result[name] = pv.get_with_metadata(form="time", use_monitor=False, timeout=2)
            finally:
                pv.disconnect()
        return result

    def cli(case, request_path, output, *, expected=0):
        command = [sys.executable, "-B", str(root / "scripts/run_digital_shadow.py"), "run-vm",
                   "--machine", "half", "--request", str(request_path),
                   "--output-root", str(output), "--vm-runtime-json", str(case / "runtime.json"),
                   "--ready-timeout", "0.3", "--capture-timeout", "2"]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / f"{output.name}_stdout.txt").write_text(completed.stdout)
        (output.parent / f"{output.name}_stderr.txt").write_text(completed.stderr)
        assert completed.returncode == expected, completed.stdout + completed.stderr
        return completed

    def check_artifacts(run):
        manifest = read_runtime_state(run / "manifest.json")
        assert manifest["status"] == "complete"
        for name, checksum in manifest["artifacts"].items():
            assert hashlib.sha256((run / name).read_bytes()).hexdigest() == checksum, name
        return manifest

    report = {"schema": "digital_shadow_vm_acceptance_v1", "status": "running",
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "ca_server_port": server_port, "ca_repeater_port": repeater_port,
              "working_points": [], "rejections": [], "services": [],
              "scope": "Private headless VM worker and CA; linear uncoupled, monoenergetic segments"}
    services = []
    worker = None
    try:
        with (directory / "services.log").open("w") as log:
            for command in ([shutil.which("caRepeater")],
                            [shutil.which("softIoc"), "-d", str(directory / "private.db")]):
                assert command[0], "EPICS executables missing"
                services.append(subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT))
            metadata([resolve_channel(context, "QT02", "K1", "vm")])
            cases = [("qt02_baseline", "QT02", "PRF06", 0),
                     ("qt02_minus", "QT02", "PRF06", -0.15),
                     ("qt02_plus", "QT02", "PRF06", 0.15),
                     ("qt01_three_screens", "QT01", "PRF08", 0)]
            for label, reference, target, delta in cases:
                case = directory / label
                case.mkdir()
                screens = ("PRF06", "PRF07", "PRF08") if reference == "QT01" else (target,)
                request = ShadowRequest(
                    model_line=f"EMIT_{reference}_{target}", reference_element=reference, reference_edge="entrance",
                    energy_mev=2200, x_moments=(4e-6, 0, 1e-8), y_moments=(3e-6, 0, 1e-8),
                    beam_basis="independent_measurement",
                    beam_provenance="Population covariance of private VM entrance macroparticles; synthetic simulation input, no target fitting",
                    diagnostics=tuple(Diagnostic(screen, channel, "mm", "rms") for screen in screens for channel in ("sigx", "sigy")))
                state, fields = prepare_model(context, request)
                state["lattice"]["QT02"]["K1"] = str(float(state["lattice"]["QT02"]["K1"]) + delta)
                for screen in screens:
                    state["lattice"][screen]["DISABLE"] = "0"
                control = state["control"]
                p0 = math.sqrt(request.energy_mev * (request.energy_mev + 2 * 0.51099895)) / 0.51099895
                control["run_setup"].update(p_central=str(p0), default_order="1", random_number_seed="12345",
                                             bpm_centroid="%s.bpmcen", centroid="%s.cen")
                control["bunched_beam"] = {"n_particles_per_bunch": "4096", "bunch": "%s.bun",
                                            "emit_x": "2e-7", "emit_y": "3e-7", "use_twiss_command_values": "1",
                                            "sigma_s": "0.001", "sigma_dp": "0"}
                write_runtime_state(case / "runtime.json", state)
                config = context.model_backend.config
                shutil.copy2(config["source_lattice"], case / "lattice_ini.lte")
                shutil.copy2(config["optics_ini_ele"], case / "one_ini.ele")
                vm_source = root / "src/virtual_machine/half_elegant/elegant"
                for filename in ("one", "cleanUp"):
                    shutil.copy2(vm_source / filename, case / filename)
                    (case / filename).chmod(0o755)
                quad_names = [resolve_channel(context, name, "K1", "vm") for name, _ in fields]
                for (name, _), pv in zip(fields, quad_names):
                    assert epics.caput(pv, float(state["lattice"][name]["K1"]), wait=True) == 1
                with (directory / f"{label}_worker.log").open("w") as worker_log:
                    worker = subprocess.Popen([sys.executable, "-B", __file__, "worker", str(case), request.model_line],
                                              stdout=worker_log, stderr=subprocess.STDOUT)
                    deadline = time.monotonic() + 15
                    while True:
                        status_path = observation_dir(case / "runtime.json") / "status.json"
                        status = read_runtime_state(status_path) if status_path.exists() else {}
                        if status.get("phase") == "Ready":
                            break
                        assert worker.poll() is None, (directory / f"{label}_worker.log").read_text()
                        assert status.get("phase") != "Failed", status
                        assert time.monotonic() < deadline, status
                        time.sleep(0.05)
                    state = read_runtime_state(case / "runtime.json")  # Worker may reconcile diagnostic definitions.
                    assert status["input_version"] == status["result_version"] == input_version(state)
                    assert status["publication"]["rms"] is True, status
                    x, xp, y, yp = columns(case / "one.bun", ("x", "xp", "y", "yp"))
                    def moments(a, b):
                        covariance = np.cov(a, b, ddof=0)
                        return (covariance[0, 0], covariance[0, 1], covariance[1, 1])
                    request = replace(request, x_moments=moments(x, xp), y_moments=moments(y, yp))
                    request_path = case / "request.json"
                    write_runtime_state(request_path, request.as_metadata())
                    names = quad_names + [resolve_channel(context, item.element, item.channel, "vm") for item in request.diagnostics]
                    before = metadata(names)
                    runtime_before = (case / "runtime.json").read_bytes()
                    completed = cli(case, request_path, case / "runs")
                    payload = json.loads(completed.stdout)
                    after = metadata(names)
                    assert all(before[name]["value"] == after[name]["value"] and
                               before[name]["timestamp"] == after[name]["timestamp"] for name in names)
                    assert (case / "runtime.json").read_bytes() == runtime_before
                    assert payload["status"] == "complete"
                    assert max(abs(row["residual"]) for row in payload["comparisons"]) < 1e-6
                    run = Path(payload["run_dir"])
                    manifest = check_artifacts(run)
                    snapshot = read_runtime_state(run / "machine_snapshot.json")
                    assert snapshot["machine"]["control_backend"] == "vm"
                    assert len(snapshot["entries"]) == len(names)
                    report["working_points"].append({"case": label, "run_dir": str(run),
                        "input_version": manifest["vm_input_version"], "quadrupoles": {name: state["lattice"][name]["K1"] for name, _ in fields},
                        "comparisons": payload["comparisons"],
                        "entrance_moments": {"x": request.x_moments, "y": request.y_moments},
                        "pv_values_and_timestamps_unchanged": True,
                        "runtime_unchanged": True, "artifacts_verified": len(manifest["artifacts"]),
                        "worker_publication": status["publication"]})
                    if label == "qt02_baseline":
                        # Inject faults only into this disposable VM/IOC fixture.
                        old_status = read_runtime_state(status_path)
                        write_runtime_state(status_path, {**old_status, "phase": "Calculating"})
                        bad = cli(case, request_path, case / "reject_not_ready", expected=1)
                        assert "did not become ready" in bad.stderr
                        report["rejections"].append({"case": "not_ready", "stderr": bad.stderr.strip()})
                        write_runtime_state(status_path, old_status)
                        quad = quad_names[0]
                        assert epics.caput(quad, before[quad]["value"] + 0.1, wait=True) == 1
                        bad = cli(case, request_path, case / "reject_different_k1", expected=1)
                        assert "does not match" in bad.stderr
                        report["rejections"].append({"case": "different_k1", "stderr": bad.stderr.strip()})
                        assert epics.caput(quad, before[quad]["value"], wait=True) == 1
                        diagnostic_pv = names[-1]
                        assert epics.caput(diagnostic_pv + ".EGU", "A", wait=True) == 1
                        bad = cli(case, request_path, case / "reject_unit", expected=1)
                        assert "unusable VM sample" in bad.stderr
                        report["rejections"].append({"case": "unit_conflict", "stderr": bad.stderr.strip()})
                        assert epics.caput(diagnostic_pv + ".EGU", "", wait=True) == 1
                        assert all(not (case / name).exists() for name in
                                   ("reject_not_ready", "reject_different_k1", "reject_unit"))
                    stop(worker)
                    report["services"].append({"kind": "worker", "pid": worker.pid, "returncode": worker.returncode})
                    worker = None
        baseline, minus, plus = report["working_points"][:3]
        assert baseline["entrance_moments"] == minus["entrance_moments"] == plus["entrance_moments"]
        assert len({row["input_version"] for row in report["working_points"]}) == 4
        for quantity in ("predicted", "measured"):
            assert minus["comparisons"][0][quantity] > baseline["comparisons"][0][quantity] > plus["comparisons"][0][quantity]
            assert minus["comparisons"][1][quantity] < baseline["comparisons"][1][quantity] < plus["comparisons"][1][quantity]
        report["fixed_entrance_scan_response_verified"] = True
        # All offline replays happen after workers and CA services stop.
        for process in reversed(services):
            stop(process)
        for row in report["working_points"]:
            run = Path(row["run_dir"])
            command = [sys.executable, "-B", __file__, "offline", "replay",
                       "--snapshot", str(run / "machine_snapshot.json"), "--request", str(run / "request.json"),
                       "--vm-evidence", str(run / "vm_capture_evidence.json"), "--output-root", str(run.parent.parent / "replay")]
            replay = subprocess.run(command, capture_output=True, text=True, timeout=20)
            assert replay.returncode == 0, replay.stdout + replay.stderr
            payload = json.loads(replay.stdout)
            assert payload["status"] == "complete" and payload["comparisons"] == row["comparisons"]
            manifest = check_artifacts(Path(payload["run_dir"]))
            original_manifest = read_runtime_state(run / "manifest.json")
            assert manifest["input_hash"] == original_manifest["input_hash"]
            row.update(offline_replay_dir=payload["run_dir"], offline_replay_without_pv_access=True)
        report["status"] = "passed"
    finally:
        if worker is not None:
            stop(worker)
        for process in reversed(services):
            stop(process)
        report["services"].extend({"kind": "ca", "pid": process.pid, "returncode": process.returncode} for process in services)
        write_runtime_state(directory / "acceptance.json", report)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "offline":
        _offline(sys.argv[2:])
    else:
        target = Path(sys.argv[2]).resolve()
        target.mkdir(parents=True, exist_ok=True)
        if mode == "worker":
            _worker(target, sys.argv[3])
        elif mode == "accept":
            _accept(target)
        else:
            raise ValueError(mode)
