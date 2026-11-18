"""Opt-in local CA + Elegant integration; all services and files are private."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("HALF_VM_SHADOW_INTEGRATION") != "1",
                    reason="Set HALF_VM_SHADOW_INTEGRATION=1 for isolated EPICS/Elegant verification")
def test_private_vm_capture_and_model_comparison(tmp_path):
    completed = subprocess.run([sys.executable, "-B", __file__, str(tmp_path)],
                               capture_output=True, text=True, timeout=45)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads((tmp_path / "cli_result.json").read_text())
    assert result["status"] == "complete"
    assert max(abs(row["residual"]) for row in result["comparisons"]) < 1e-6


@pytest.mark.skipif(os.environ.get("HALF_VM_SHADOW_INTEGRATION") != "1",
                    reason="Set HALF_VM_SHADOW_INTEGRATION=1 for isolated EPICS/Elegant verification")
def test_private_vm_gui_capture_and_model_comparison(tmp_path):
    completed = subprocess.run([sys.executable, "-B", __file__, str(tmp_path), "--gui", "--four-screens"],
                               capture_output=True, text=True, timeout=45)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads((tmp_path / "cli_result.json").read_text())
    assert result["status"] == "complete"
    assert {row["key"] for row in result["comparisons"]} == {
        f"{screen}/{channel}" for screen in ("PRF06", "PRF07", "PRF08", "PRF09")
        for channel in ("sigx", "sigy")
    }
    assert max(abs(row["residual"]) for row in result["comparisons"]) < 1e-6


def _gui_capture(root, directory, request_path, runtime):
    import time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.digital_shadow.main import DigitalShadowWindow
    app = QApplication.instance() or QApplication([])
    window = DigitalShadowWindow(output_root=directory / "runs")
    try:
        window.show()
        window.load_request_file(request_path)
        window.mode.setCurrentIndex(1)
        window.runtime_edit.setText(str(runtime))
        window.run_button.click()
        deadline = time.monotonic() + 15
        while window.controller.busy:
            assert time.monotonic() < deadline, window.log.toPlainText()
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert window.controller.record["status"] == "complete", window.log.toPlainText()
        expected_rows = len(json.loads(request_path.read_text())["diagnostics"])
        assert window.results.rowCount() == expected_rows
        archive = window.current_archive
        window.grab().save(str(directory / "gui_vm_complete.png"))
        return {**archive["result"], "run_dir": archive["run_dir"]}
    finally:
        window.controller.shutdown_now()
        window.close()
        app.processEvents()


def _run_private_vm(directory, *, gui=False, four_screens=False):
    from datetime import datetime, timezone
    from dataclasses import replace
    import math
    import socket
    import time

    # Set discovery isolation before importing pyepics in this fresh process.
    def port():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(("127.0.0.1", 0))
            return udp.getsockname()[1]
    server_port, repeater_port = port(), port()
    os.environ.update(EPICS_CA_AUTO_ADDR_LIST="NO", EPICS_CA_ADDR_LIST="127.0.0.1",
                      EPICS_CA_SERVER_PORT=str(server_port), EPICS_CAS_SERVER_PORT=str(server_port),
                      EPICS_CA_REPEATER_PORT=str(repeater_port), EPICS_CAS_INTF_ADDR_LIST="127.0.0.1",
                      EPICS_CAS_AUTO_BEACON_ADDR_LIST="NO", EPICS_CAS_BEACON_ADDR_LIST="127.0.0.1")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts/python_imports"))
    import epics
    import numpy as np
    from half_linac.src.shared.digital_shadow.models import Diagnostic, ShadowRequest
    from half_linac.src.shared.digital_shadow.pipeline import prepare_model
    from half_linac.src.shared.elegant_backend import ElegantParser, VmPublisher, build_vm_publish_plan
    from half_linac.src.shared.elegant_runtime import run_elegant_input
    from half_linac.src.shared.machine_profile.loader import load_model_context
    from half_linac.src.shared.machine_profile.models import ControlBackendConfig
    from half_linac.src.shared.machine_profile.resolver import resolve_channel
    from half_linac.src.shared.runtime_state import write_runtime_state
    from half_linac.src.virtual_machine.workbench_data import columns, collect_results, input_version, observation_dir

    context = replace(load_model_context("half"), control_backend=ControlBackendConfig("vm"))
    screens = ("PRF06", "PRF07", "PRF08", "PRF09") if four_screens else ("PRF06",)
    request = ShadowRequest(
        model_line="ALL_MAIN" if four_screens else "EMIT_QT02_PRF06",
        reference_element="QT01" if four_screens else "QT02", reference_edge="entrance",
        energy_mev=2200, x_moments=(4e-6, 0, 1e-8), y_moments=(3e-6, 0, 1e-8),
        beam_basis="independent_measurement", beam_provenance="Entrance macroparticle moments from isolated VM tracking input, not target screen",
        diagnostics=tuple(Diagnostic(screen, channel, "mm", "rms") for screen in screens for channel in ("sigx", "sigy")),
    )
    state, fields = prepare_model(context, request)
    if four_screens:
        names = state["usedline"]
        state["usedline"] = names[names.index("QT01"):names.index("PRF09") + 1]
    state["lattice"]["QT02"]["K1"] = str(float(state["lattice"]["QT02"]["K1"]) + 0.15)
    for screen in screens:
        state["lattice"][screen]["DISABLE"] = "0"
    p0 = math.sqrt(request.energy_mev * (request.energy_mev + 2 * 0.51099895)) / 0.51099895
    control = state["control"]
    control["run_setup"].update(p_central=str(p0), default_order="1", random_number_seed="12345")
    control["bunched_beam"] = {"n_particles_per_bunch": "4096", "bunch": "%s.bun",
                                "emit_x": "2e-7", "emit_y": "3e-7", "use_twiss_command_values": "1",
                                "sigma_s": "0.001", "sigma_dp": "0"}
    runtime = directory / "runtime.json"
    write_runtime_state(runtime, state)
    config = context.model_backend.config
    parser = ElegantParser(config["source_lattice"], config["optics_ini_ele"], request.model_line)
    calculation_started = time.monotonic()
    parser.json_to_lte_ele(directory / "lattice.lte", directory / "one.ele", runtime)
    run_elegant_input("one.ele", "vm.log", workdir=directory, timeout_s=10)
    x, xp, y, yp = columns(directory / "one.bun", ("x", "xp", "y", "yp"))
    def moments(a, b):
        covariance = np.cov(a, b, ddof=0)
        return (covariance[0, 0], covariance[0, 1], covariance[1, 1])
    request = replace(request, x_moments=moments(x, xp), y_moments=moments(y, yp))
    request_path = directory / "request.json"
    write_runtime_state(request_path, request.as_metadata())

    names = [resolve_channel(context, quad, "K1") for quad, _ in fields]
    names += [resolve_channel(context, screen, channel) for screen in screens for channel in ("sigx", "sigy")]
    db = directory / "private.db"
    templates = root / "src/softIOC/halflinac/db"
    records = []
    def record(template, **macros):
        text = (templates / template).read_text()
        for key, value in macros.items():
            text = text.replace(f"$({key})", value)
        assert "$(" not in text
        records.append(text)
    for quad, _ in fields:
        record("quad.template", RECORD=f"VMIOC:QUAD:{quad}:K1", K1ALIAS=resolve_channel(context, quad, "K1"))
    for screen in screens:
        for channel in ("sigx", "sigy"):
            record(f"flag_{channel}.template", **{
                f"{channel.upper()}RECORD": f"VMIOC:FLAG:{screen}:{channel.upper()}",
                f"{channel.upper()}ALIAS": resolve_channel(context, screen, channel),
            })
    db.write_text("\n".join(records))
    soft_ioc, repeater = shutil.which("softIoc"), shutil.which("caRepeater")
    if not soft_ioc or not repeater:
        raise RuntimeError("Integration requires softIoc and caRepeater in PATH")
    processes = []
    with (directory / "ioc.log").open("w") as log:
        try:
            processes.append(subprocess.Popen([repeater], stdout=log, stderr=subprocess.STDOUT))
            processes.append(subprocess.Popen([soft_ioc, "-d", str(db)], stdin=subprocess.PIPE,
                                              stdout=log, stderr=subprocess.STDOUT))
            deadline = time.monotonic() + 5
            pvs = [epics.PV(name, auto_monitor=False) for name in names]
            for pv in pvs:
                assert pv.wait_for_connection(timeout=max(0.01, deadline - time.monotonic()))
            for quad, _ in fields:
                assert epics.caput(resolve_channel(context, quad, "K1"), float(state["lattice"][quad]["K1"]), wait=True) == 1
            plan = build_vm_publish_plan(context.profile)
            assert VmPublisher().publish_watch_rms(plan, lattice=state["lattice"], usedline=state["usedline"], elegant_dir=directory)
            result = collect_results(state, directory)
            result.update(input_state=state, input_version=input_version(state), session="private-vm", calculation=1)
            status = {"phase": "Ready", "input_version": input_version(state), "result_version": input_version(state),
                      "session": "private-vm", "calculation": 1, "publication": {"rms": True},
                      "completed_at": datetime.now(timezone.utc).isoformat(), "elapsed": time.monotonic() - calculation_started}
            write_runtime_state(observation_dir(runtime) / "result.json", result)
            write_runtime_state(observation_dir(runtime) / "status.json", status)
            time.sleep(6)  # Unchanged VM diagnostics remain valid beyond the normal replay age window.
            if gui:
                payload = _gui_capture(root, directory, request_path, runtime)
                (directory / "cli_result.json").write_text(json.dumps(payload))
            else:
                completed = subprocess.run([
                    sys.executable, "-B", str(root / "scripts/run_digital_shadow.py"), "run-vm", "--machine", "half",
                    "--request", str(request_path), "--output-root", str(directory / "runs"),
                    "--vm-runtime-json", str(runtime), "--ready-timeout", "2",
                ], capture_output=True, text=True, timeout=15)
                if completed.returncode:
                    raise RuntimeError(completed.stdout + completed.stderr)
                (directory / "cli_result.json").write_text(completed.stdout)
                payload = json.loads(completed.stdout)
            replay_root = Path(payload["run_dir"])
            # Confirm the archived result can be replayed with no live reads.
            replay = subprocess.run([
                sys.executable, "-B", str(root / "scripts/run_digital_shadow.py"), "replay",
                "--snapshot", str(replay_root / "machine_snapshot.json"), "--request", str(request_path),
                "--vm-evidence", str(replay_root / "vm_capture_evidence.json"), "--output-root", str(directory / "replay"),
            ], capture_output=True, text=True, timeout=10)
            assert replay.returncode == 0, replay.stderr
            assert json.loads(replay.stdout)["status"] == "complete"
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


if __name__ == "__main__":
    target = Path(sys.argv[1])
    target.mkdir(parents=True, exist_ok=True)
    _run_private_vm(target, gui="--gui" in sys.argv[2:], four_screens="--four-screens" in sys.argv[2:])
