"""One asynchronous CLI worker, immutable inputs, and owned process-tree cleanup."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import signal
import sys
import uuid

from PyQt5.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from half_linac.src.shared.machine_state import load_snapshot, snapshot_to_dict
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state


def read_object(path):
    value = read_runtime_state(path)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {Path(path).name}")
    return value


def read_archive(directory):
    directory = Path(directory).resolve()
    if directory.is_file():
        snapshot = load_snapshot(directory)
        return {"directory": str(directory.parent), "backend": snapshot.backend, "machine": snapshot.machine_id,
                "status": "snapshot", "snapshot": read_runtime_state(directory), "request": None,
                "result": None, "manifest": None, "snapshot_path": str(directory)}
    job = read_object(directory / "job.json") if (directory / "job.json").exists() else None
    if job and job.get("schema") != "digital_shadow_gui_job_v1":
        raise ValueError("Unsupported GUI job record")
    if job and job.get("run_dir"):
        run = Path(job["run_dir"]).resolve()
        if not run.is_relative_to(directory):
            raise ValueError("Job result is outside its run directory")
    else:
        run = directory
    snapshot_path = run / "machine_snapshot.json"
    snapshot = load_snapshot(snapshot_path) if snapshot_path.exists() else None
    manifest = read_object(run / "manifest.json") if (run / "manifest.json").exists() else None
    if manifest and manifest.get("schema") != "digital_shadow_run_v1":
        raise ValueError("Unsupported shadow manifest")
    if manifest and not isinstance(manifest.get("artifacts", {}), dict):
        raise ValueError("Malformed archive artifact index")
    if not snapshot and not manifest and not job:
        raise ValueError("Select a digital shadow archive or MachineStateSnapshot")
    request = read_object(run / "request.json") if (run / "request.json").exists() else None
    result = read_object(run / "result.json") if (run / "result.json").exists() else None
    if result and (not manifest or result.get("input_hash") != manifest.get("input_hash")
                   or result.get("status") != manifest.get("status") or not isinstance(result.get("comparisons"), list)):
        raise ValueError("Result does not match its manifest")
    if result and any(not isinstance(row, dict) for row in result["comparisons"]):
        raise ValueError("Malformed diagnostic comparison")
    if manifest:
        for name in ("machine_snapshot.json", "request.json", "result.json", "vm_capture_evidence.json", "real_capture_evidence.json"):
            expected = manifest.get("artifacts", {}).get(name)
            if expected and hashlib.sha256((run / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"Archive integrity check failed: {name}")
    real_path = run / "real_capture_evidence.json"
    real_evidence = read_object(real_path) if real_path.exists() else None
    if real_evidence and real_evidence.get("schema") != "digital_shadow_real_capture_v1":
        raise ValueError("Unsupported real capture evidence")
    return {"directory": str(directory), "run_dir": str(run), "backend": snapshot.backend if snapshot else (job or {}).get("backend", "unknown"),
            "machine": snapshot.machine_id if snapshot else "half", "status": (job or manifest or {}).get("status", "inputs"),
            "snapshot": read_runtime_state(snapshot_path) if snapshot else None,
            "snapshot_path": str(snapshot_path) if snapshot else None,
            "request": request, "result": result, "manifest": manifest, "job": job,
            "real_evidence_path": str(real_path) if real_evidence else None}


class ShadowJobController(QObject):
    message = pyqtSignal(str)
    state_changed = pyqtSignal(str)
    completed = pyqtSignal(object)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.process = None
        self.job_dir = None
        self.record = None
        self._stopping = False
        self._cleaned = False
        self._pid = 0
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._force_stop)

    @property
    def busy(self):
        return self.process is not None

    def start(self, request, output_root, *, mode, snapshot_path=None, evidence_path=None, runtime_json=None,
              ready_timeout=10.0, capture_timeout=5.0):
        if self.busy:
            raise ValueError("A shadow task is already running")
        if mode not in {"replay", "run-vm", "demo-real", "run-real"}:
            raise ValueError("Choose VM replay or VM capture")
        if mode == "replay":
            snapshot = load_snapshot(snapshot_path)
            if snapshot.machine_id != "half" or snapshot.backend not in {"vm", "real"} or (snapshot.backend == "real" and not evidence_path):
                raise ValueError("Real or non-HALF snapshots are view-only; choose a HALF VM snapshot")
        evidence = read_object(evidence_path) if evidence_path and mode == "replay" else None
        if mode == "replay" and snapshot.backend == "real":
            from dataclasses import replace
            from half_linac.src.shared.machine_profile.loader import load_model_context
            from half_linac.src.shared.machine_profile.models import ControlBackendConfig
            from half_linac.src.shared.digital_shadow.real_capture import verify_real_evidence
            context = replace(load_model_context("half", request.model_backend), control_backend=ControlBackendConfig("real"))
            verify_real_evidence(context, snapshot, request, evidence)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        directory = Path(output_root).resolve() / f"job_{stamp}_{uuid.uuid4().hex[:10]}"
        directory.mkdir(parents=True, exist_ok=False)
        write_runtime_state(directory / "request.json", request.as_metadata())
        arguments = [mode, "--request", str(directory / "request.json"), "--output-root", str(directory)]
        if mode == "replay":
            write_runtime_state(directory / "machine_snapshot.json", snapshot_to_dict(snapshot))
            arguments.extend(["--snapshot", str(directory / "machine_snapshot.json")])
            if evidence_path:
                name = "real_capture_evidence.json" if snapshot.backend == "real" else "vm_capture_evidence.json"
                write_runtime_state(directory / name, evidence)
                arguments.extend(["--real-evidence" if snapshot.backend == "real" else "--vm-evidence", str(directory / name)])
        else:
            arguments.extend(["--machine", "half", "--capture-timeout", str(capture_timeout)])
            if mode == "run-real":
                arguments.append("--connect-real")
            if mode == "run-vm":
                arguments.extend(["--ready-timeout", str(ready_timeout)])
            if runtime_json and mode == "run-vm":
                arguments.extend(["--vm-runtime-json", str(Path(runtime_json).resolve())])
        self.job_dir = directory
        backend = snapshot.backend if mode == "replay" else "real" if mode in {"demo-real", "run-real"} else "vm"
        self.record = {"schema": "digital_shadow_gui_job_v1", "status": "running", "mode": mode, "backend": backend,
                       "started_at": datetime.now(timezone.utc).isoformat(), "run_dir": None}
        self._save()
        self._stopping = self._cleaned = False
        self._pid = 0
        process = QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(self.root))
        environment = QProcessEnvironment.systemEnvironment()
        for name, value in (("HALF_LINAC_MACHINE_ID", "half"), ("HALF_MACHINE_ID", "half"),
                            ("HALF_LINAC_CONTROL_BACKEND", "vm"), ("HALF_CONTROL_BACKEND", "vm")):
            environment.insert(name, value)
        process.setProcessEnvironment(environment)
        process.setProgram(sys.executable)
        process.setArguments(["-u", str(Path(__file__).with_name("worker.py")), str(directory), *arguments])
        process.started.connect(lambda: self._started(process))
        process.readyReadStandardOutput.connect(lambda: self._drain(process, False))
        process.readyReadStandardError.connect(lambda: self._drain(process, True))
        process.finished.connect(lambda code, status: self._finished(process, code))
        process.errorOccurred.connect(lambda error: self._error(process, error))
        self.state_changed.emit("Running")
        process.start()
        return directory

    def _started(self, process):
        if process is self.process:
            self._pid = int(process.processId())

    def _drain(self, process, stderr):
        if process is not self.process:
            return
        data = bytes(process.readAllStandardError() if stderr else process.readAllStandardOutput())
        if data:
            with (self.job_dir / ("stderr.log" if stderr else "stdout.log")).open("ab") as stream:
                stream.write(data)
            self.message.emit(data.decode("utf-8", errors="replace"))

    def _signal_group(self, number):
        marker = self.job_dir / "worker.pgid" if self.job_dir else None
        try:
            if self._pid > 0 and marker and marker.exists() and int(marker.read_text()) == self._pid:
                os.killpg(self._pid, number)
                return
        except (OSError, ValueError):
            pass
        if self.process and self.process.state() != QProcess.NotRunning:
            self.process.kill() if number == signal.SIGKILL else self.process.terminate()

    def stop(self):
        if not self.busy or self._stopping:
            return
        self._stopping = True
        self.state_changed.emit("Stopping")
        self._signal_group(signal.SIGTERM)
        self._stop_timer.start(1000)

    def _force_stop(self):
        if not self.busy:
            return
        self._cleaned = True
        self._signal_group(signal.SIGKILL)  # Also clean descendants after the worker itself has exited.
        if self.process.state() == QProcess.NotRunning:
            self._finish(self.process.exitCode())

    def shutdown_now(self):
        if self.busy:
            self._stopping = self._cleaned = True
            self._stop_timer.stop()
            self._signal_group(signal.SIGKILL)
            self.process.waitForFinished(1000)
            if self.busy:
                self._finish(-1)

    def _finished(self, process, code):
        if process is not self.process:
            return
        self._drain(process, False)
        self._drain(process, True)
        if self._stopping and not self._cleaned:
            return
        self._signal_group(signal.SIGKILL)
        self._finish(code)

    def _error(self, process, error):
        if process is self.process and error == QProcess.FailedToStart:
            self.record["error"] = process.errorString()
            self._finish(-1)

    def _save(self):
        write_runtime_state(self.job_dir / "job.json", self.record)

    def _finish(self, code):
        if not self.busy:
            return
        self._stop_timer.stop()
        self.record.update(status="interrupted" if self._stopping else "failed", exit_code=code,
                           finished_at=datetime.now(timezone.utc).isoformat())
        manifests = sorted(self.job_dir.glob("shadow_*/manifest.json"))
        if len(manifests) == 1:
            self.record["run_dir"] = str(manifests[0].parent)
            try:
                archive = read_archive(manifests[0].parent)
                if not self._stopping and code in (0, 2) and archive["status"] in {"complete", "partial"} and archive["result"]:
                    self.record["status"] = archive["status"]
                elif archive["manifest"]:
                    self.record["error"] = archive["manifest"].get("error", {}).get("message", "Worker did not complete")
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.record["error"] = str(error)
        if self.record["status"] == "failed" and not self.record.get("error"):
            log = self.job_dir / "stderr.log"
            self.record["error"] = log.read_text(errors="replace")[-8000:] if log.exists() else "Worker failed; see task logs"
        self._save()
        process, self.process = self.process, None
        process.deleteLater()
        self.state_changed.emit(self.record["status"].title())
        self.completed.emit({**self.record, "job_dir": str(self.job_dir)})
