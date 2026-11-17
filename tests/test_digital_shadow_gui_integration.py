"""Opt-in GUI/Elegant and process-tree lifecycle verification."""
import os
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HALF_VM_SHADOW_INTEGRATION") != "1",
                               reason="Enable explicitly for GUI/Elegant verification")


def wait_for(app, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        if app is not None:
            app.processEvents()
        assert time.monotonic() < deadline, "GUI operation did not finish"
        time.sleep(0.01)
    if app is not None:
        app.processEvents()


def alive(pid):
    try:
        status = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1]
        return status[0] != "Z"
    except FileNotFoundError:
        return False


def slow_elegant(tmp_path, monkeypatch):
    executable = tmp_path / "bin/elegant"
    executable.parent.mkdir()
    executable.write_text(f"#!{sys.executable}\n" + '''
import json, os, signal, subprocess, sys, time
from pathlib import Path
target = Path(os.environ['SHADOW_SLOW_PID_FILE'])
ready = target.with_suffix('.ready')
child = subprocess.Popen([sys.executable, '-c',
    "import signal,sys,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(sys.argv[1]).write_text('ready'); time.sleep(300)", str(ready)])
while not ready.exists(): time.sleep(0.01)
target.write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(300)
''')
    executable.chmod(0o755)
    target = tmp_path / "pids.json"
    monkeypatch.setenv("SHADOW_SLOW_PID_FILE", str(target))
    monkeypatch.setenv("PATH", str(executable.parent) + os.pathsep + os.environ["PATH"])
    return target


@pytest.fixture
def gui(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.digital_shadow.main import DigitalShadowWindow
    app = QApplication.instance() or QApplication([])
    window = DigitalShadowWindow(output_root=tmp_path / "runs")
    window.show()
    window.example_button.click()
    try:
        yield app, window
    finally:
        window.controller.shutdown_now()
        window.close()
        app.processEvents()


def test_gui_runs_real_elegant_and_preserves_inputs(gui, tmp_path):
    app, window = gui
    snapshot = Path(window.snapshot_edit.text())
    original = snapshot.read_bytes()
    window.run_button.click()
    assert window.controller.busy and not window.form.isEnabled()
    wait_for(app, lambda: not window.controller.busy)
    assert window.controller.record["status"] == "complete", window.log.toPlainText()
    assert snapshot.read_bytes() == original
    assert window.results.rowCount() == 2
    assert window.history.count() == 1
    assert window.form.isEnabled()
    assert "SYNTHETIC" in window.result_banner.text()
    window.grab().save(str(tmp_path / "gui_complete.png"))
    archive = window.current_archive
    from half_linac.src.apps.digital_shadow.jobs import read_archive
    loaded = read_archive(archive["run_dir"])
    assert loaded["result"]["status"] == "complete"
    window.history.setCurrentRow(0)
    assert window.use_inputs_button.isEnabled()
    window._use_archive_inputs()
    window.run_button.click()
    assert not window.use_inputs_button.isEnabled()
    wait_for(app, lambda: not window.controller.busy)
    assert window.controller.record["status"] == "complete"
    assert window.history.count() == 2


@pytest.mark.parametrize("action", ["stop", "close"])
def test_stop_and_close_clean_stubborn_descendants(gui, tmp_path, monkeypatch, action):
    app, window = gui
    target = slow_elegant(tmp_path, monkeypatch)
    window.run_button.click()
    wait_for(app, target.exists)
    pids = json.loads(target.read_text())
    assert all(alive(pid) for pid in pids)
    if action == "stop":
        window.stop_button.click()
    else:
        window.close()
    wait_for(app, lambda: not window.controller.busy, timeout=5)
    wait_for(app, lambda: all(not alive(pid) for pid in pids), timeout=3)
    assert window.controller.record["status"] == "interrupted"
    assert window.results.rowCount() == 0
    assert json.loads((window.controller.job_dir / "job.json").read_text())["status"] == "interrupted"
    if action == "close":
        assert not window.isVisible()


def test_control_room_sigterm_path_cleans_worker_tree(tmp_path, monkeypatch):
    target = slow_elegant(tmp_path, monkeypatch)
    from half_linac.src.shared.process_runtime import ManagedProcessGroup
    manager = ManagedProcessGroup()
    process = manager.start_process(key="signal-probe", label="Digital Shadow test", cwd=str(tmp_path),
        cmd=[sys.executable, "-B", __file__, "--signal-probe", str(tmp_path / "probe")])
    assert process is not None
    try:
        wait_for(None, target.exists)
        pids = json.loads(target.read_text())
        assert manager.stop_process("signal-probe")
        assert process.wait(timeout=4) == 0
        wait_for(None, lambda: all(not alive(pid) for pid in pids), timeout=3)
        jobs = list((tmp_path / "probe").glob("job_*/job.json"))
        assert len(jobs) == 1 and json.loads(jobs[0].read_text())["status"] == "interrupted"
    finally:
        manager.shutdown()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        for marker in (tmp_path / "probe").glob("job_*/worker.pgid"):
            try:
                os.killpg(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == "__main__" and sys.argv[1] == "--signal-probe":
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts/python_imports"))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt5.QtCore import QTimer
    from half_linac.src.apps.digital_shadow import main as gui_module
    base = gui_module.DigitalShadowWindow
    class Probe(base):
        def __init__(self):
            super().__init__(output_root=Path(sys.argv[2]))
            self.load_example()
            QTimer.singleShot(0, self.run_task)
    gui_module.DigitalShadowWindow = Probe
    raise SystemExit(gui_module.main([]))
