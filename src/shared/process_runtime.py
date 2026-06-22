import os
import signal
import time
from subprocess import Popen, TimeoutExpired

from PyQt5.QtWidgets import QApplication


class ManagedProcessGroup:
    def __init__(self, notify=None, start_timeout_s=0.3, stop_timeout_s=2.0):
        self.notify = notify or (lambda message: None)
        self.start_timeout_s = start_timeout_s
        self.stop_timeout_s = stop_timeout_s
        self.processes = {}
        self.process_labels = {}
        self._is_shutting_down = False

    def install_signal_handlers(self):
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)

    def _handle_shutdown_signal(self, signum, frame):
        self.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def prune_finished_processes(self):
        for key, proc in list(self.processes.items()):
            if proc.poll() is not None:
                self.processes.pop(key, None)
                label = self.process_labels.pop(key, key)
                if proc.returncode:
                    self.notify(f"{label} exited with code {proc.returncode}.")

    def is_running(self, key):
        proc = self.processes.get(key)
        return proc is not None and proc.poll() is None

    def start_process(self, key, label, cmd, cwd, expect_running=True):
        self.prune_finished_processes()
        if self.is_running(key):
            self.notify(f"{label} is already running.")
            return None

        proc = Popen(
            cmd,
            cwd=cwd,
            shell=False,
            start_new_session=True,
        )

        if not expect_running:
            self.processes[key] = proc
            self.process_labels[key] = label
            return proc

        try:
            proc.wait(timeout=self.start_timeout_s)
        except TimeoutExpired:
            self.processes[key] = proc
            self.process_labels[key] = label
            return proc

        self.notify(f"Failed to start {label} (exit code {proc.returncode}).")
        return None

    def _signal_process_group(self, proc, sig):
        if proc.poll() is not None:
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

    def stop_all(self):
        self.prune_finished_processes()
        procs = list(self.processes.values())

        for proc in procs:
            self._signal_process_group(proc, signal.SIGTERM)

        deadline = time.time() + self.stop_timeout_s
        while time.time() < deadline:
            if all(proc.poll() is not None for proc in procs):
                break
            time.sleep(0.1)

        for proc in procs:
            if proc.poll() is None:
                self._signal_process_group(proc, signal.SIGKILL)

        self.processes.clear()
        self.process_labels.clear()

    def shutdown(self):
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self.stop_all()
