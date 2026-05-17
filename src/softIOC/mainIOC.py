import os
import signal
import time
from pathlib import Path
from subprocess import Popen, TimeoutExpired

import epics
import epics.ca

from pv_server import pv_server

import half_linac.setup as st
from half_linac.src.virtual_machine.half_elegant.elegant_parser import elegant_parser
from half_linac.src.virtual_machine.half_elegant.runtime_state import ensure_runtime_state


IOC_READY_TIMEOUT_S = 15.0
IOC_READY_POLL_INTERVAL_S = 0.2
IOC_READY_CONNECTION_TIMEOUT_S = 0.5
IOC_READY_SAMPLE_SIZE = 5
IOC_STOP_TIMEOUT_S = 3.0
MONITOR_INTERVAL_S = 1.0

_ioc_process = None
_stop_requested = False


def _handle_shutdown_signal(signum, frame):
    global _stop_requested
    _stop_requested = True


def _stop_ioc_process():
    global _ioc_process

    if _ioc_process is None or _ioc_process.poll() is not None:
        return

    _signal_process_group(_ioc_process, signal.SIGTERM)
    try:
        _ioc_process.wait(timeout=IOC_STOP_TIMEOUT_S)
    except TimeoutExpired:
        _signal_process_group(_ioc_process, signal.SIGKILL)
        _ioc_process.wait()


def _signal_process_group(proc, sig):
    if proc is None or proc.poll() is not None:
        return

    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return
    except Exception:
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()


def _start_ioc_process(iocpath):
    return Popen(
        ["bash", "runMe"],
        cwd=str(iocpath),
        shell=False,
        start_new_session=True,
    )


def _wait_for_ioc_ready(pv_names):
    sample_pv_names = pv_names[:IOC_READY_SAMPLE_SIZE]
    if not sample_pv_names:
        return

    sample_pvs = [
        epics.PV(
            pv_name,
            auto_monitor=False,
            connection_timeout=IOC_READY_CONNECTION_TIMEOUT_S,
        )
        for pv_name in sample_pv_names
    ]

    deadline = time.monotonic() + IOC_READY_TIMEOUT_S
    last_ca_error = None

    while time.monotonic() < deadline:
        if _ioc_process is not None and _ioc_process.poll() is not None:
            raise RuntimeError(f"softIOC exited early with code {_ioc_process.returncode}")

        try:
            if all(
                pv.wait_for_connection(timeout=IOC_READY_CONNECTION_TIMEOUT_S)
                for pv in sample_pvs
            ):
                return
        except epics.ca.ChannelAccessException as exc:
            last_ca_error = exc

        time.sleep(IOC_READY_POLL_INTERVAL_S)

    if last_ca_error is not None:
        raise RuntimeError(
            f"softIOC did not become CA-reachable within {IOC_READY_TIMEOUT_S:.1f}s: {last_ca_error}"
        )

    raise RuntimeError(
        f"softIOC did not expose the expected PVs within {IOC_READY_TIMEOUT_S:.1f}s."
    )


def main():
    global _ioc_process

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    root = Path(st.rootpath)
    lattice_file = root / "src/virtual_machine/half_elegant/elegant/lattice_ini.lte"
    ele_file = root / "src/virtual_machine/half_elegant/elegant/one_ini.ele"
    jsonpath = root / "src/virtual_machine/half_elegant/halflinac.json"
    iocpath = root / "src/softIOC/halflinac"

    def build_initial_state():
        return elegant_parser(str(lattice_file), str(ele_file), "ALL").build_runtime_state()

    if ensure_runtime_state(jsonpath, build_initial_state):
        print("Initialized runtime lattice JSON.")
    else:
        print("Using existing runtime lattice JSON.")

    server = pv_server(str(jsonpath), str(iocpath))
    server.gen_substitution_file()
    server.prepare_initial_pvs()

    _ioc_process = _start_ioc_process(iocpath)
    print("softIOC process started")

    _wait_for_ioc_ready(server.pvl)
    print("softIOC PVs are reachable")

    server.init_lattice_pv()
    server.monitor_json()
    print("softIOC is monitoring PV changes")

    try:
        while not _stop_requested:
            if _ioc_process.poll() is not None:
                raise RuntimeError(f"softIOC exited with code {_ioc_process.returncode}")
            time.sleep(MONITOR_INTERVAL_S)
    finally:
        _stop_ioc_process()

    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        _stop_ioc_process()
        print(f"softIOC manager failed: {exc}")
        raise
