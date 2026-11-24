"""One asynchronous CLI worker, immutable inputs, and owned process-tree cleanup."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import uuid

from PyQt5.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from half_linac.src.shared.digital_shadow.beam_state import load_beam_state
from half_linac.src.shared.digital_shadow.impact_model import DECK
from half_linac.src.shared.digital_shadow.particle_capture import CAPTURE_COMMAND, CAPTURE_SOURCES, EVIDENCE_FILE, FOLLOW_COMMAND
from half_linac.src.shared.digital_shadow.particle_follow import FOLLOW_EVENT
from half_linac.src.shared.digital_shadow.particle_model import PARTICLE_RUN_KIND, PROGRESS_PREFIX, ParticleModelRequest
from half_linac.src.shared.digital_shadow.session import file_digest
from half_linac.src.shared.machine_state import load_snapshot, snapshot_to_dict
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state


def read_object(path):
    value = read_runtime_state(path)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {Path(path).name}")
    return value


def read_archive(directory, *, verify_particle_inputs=False):
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
    if job and job.get("status") == "interrupted" and (manifest or {}).get("scope") in ("preinjector", "coupled"):
        result = None
    if result and (not manifest or result.get("input_hash") != manifest.get("input_hash")
                   or result.get("status") != manifest.get("status") or not isinstance(result.get("comparisons"), list)):
        raise ValueError("Result does not match its manifest")
    if result and any(not isinstance(row, dict) for row in result["comparisons"]):
        raise ValueError("Malformed diagnostic comparison")
    if result and manifest.get("kind") == PARTICLE_RUN_KIND and (
        result.get("kind") != PARTICLE_RUN_KIND or not isinstance(result.get("predictions"), list)
        or any(not isinstance(row, dict) for row in result["predictions"])
    ):
        raise ValueError("Malformed particle model predictions")
    if manifest:
        for name in ("machine_snapshot.json", "request.json", "result.json", "vm_capture_evidence.json",
                     "real_capture_evidence.json", "image_index.json", "image_comparisons.json", EVIDENCE_FILE):
            expected = manifest.get("artifacts", {}).get(name)
            if expected and file_digest(run / name) != expected:
                raise ValueError(f"Archive integrity check failed: {name}")
        if verify_particle_inputs and manifest.get("kind") == PARTICLE_RUN_KIND and request:
            particle_request = ParticleModelRequest.from_dict(request, base=run)
            for input_path in (particle_request.beam_path, particle_request.handoff_path, particle_request.capture_evidence_path):
                if input_path is None:
                    continue
                path = Path(input_path)
                if not path.is_relative_to(run):
                    raise ValueError("Particle input is outside its archive")
                expected = manifest["artifacts"].get(path.relative_to(run).as_posix())
                if not expected or file_digest(path) != expected:
                    raise ValueError(f"Archive integrity check failed: {path.name}")
            if particle_request.scope == "preinjector" and result:
                if "deck_sha256" in manifest and (
                    manifest.get("impact_variant") != particle_request.impact_variant
                    or file_digest(run / "impact" / DECK) != manifest["deck_sha256"]
                ):
                    raise ValueError(f"Archive integrity check failed: {DECK}")
                for field in ("beam_path", "handoff_path"):
                    path = (run / result[field]).resolve()
                    if not path.is_relative_to(run):
                        raise ValueError("Pre-injector output is outside its archive")
                    expected = manifest["artifacts"].get(path.relative_to(run).as_posix())
                    if not expected or file_digest(path) != expected:
                        raise ValueError(f"Archive integrity check failed: {path.name}")
                load_beam_state(run / result["beam_path"])
            if particle_request.scope == "coupled" and result:
                for name in ("upstream", "downstream"):
                    stage = (run / name).resolve()
                    key = f"{name}/manifest.json"
                    if stage != run / name or manifest["stages"][name]["directory"] != name:
                        raise ValueError(f"Invalid coupled stage directory: {name}")
                    if file_digest(stage / "manifest.json") != manifest["artifacts"].get(key):
                        raise ValueError(f"Archive integrity check failed: {key}")
                    stage_archive = read_archive(stage, verify_particle_inputs=True)
                    if name == "upstream" and "deck_sha256" in manifest and (
                        stage_archive["manifest"].get("impact_variant") != particle_request.impact_variant
                        or stage_archive["manifest"].get("deck_sha256") != manifest["deck_sha256"]
                    ):
                        raise ValueError(f"Archive integrity check failed: {name}/{DECK}")
    real_path = run / "real_capture_evidence.json"
    real_evidence = read_object(real_path) if real_path.exists() else None
    if real_evidence and real_evidence.get("schema") != "digital_shadow_real_capture_v1":
        raise ValueError("Unsupported real capture evidence")
    image_path = run / "image_index.json"
    if result and "image_comparisons" in result:
        comparisons = result["image_comparisons"]
        saved = read_object(run / "image_comparisons.json")
        if (not isinstance(comparisons, list) or any(not isinstance(row, dict) for row in comparisons)
                or saved.get("schema") != "digital_shadow_image_comparison_v1"
                or saved.get("input_hash") != result["input_hash"] or saved.get("comparisons") != comparisons
                or saved.get("status") != result.get("image_comparison_status")):
            raise ValueError("Image comparison does not match its archived result")
    images = read_object(image_path) if image_path.exists() else None
    if images:
        from dataclasses import replace
        from half_linac.src.shared.machine_profile.loader import load_model_context
        from half_linac.src.shared.machine_profile.models import ControlBackendConfig
        from half_linac.src.shared.digital_shadow.image_capture import verify_image_bundle
        if snapshot is None:
            raise ValueError("Image archive has no bound scalar snapshot")
        context = replace(load_model_context(snapshot.machine_id), control_backend=ControlBackendConfig(snapshot.backend))
        verify_image_bundle(context, snapshot, images)
    backend = "offline" if (manifest or {}).get("kind") == PARTICLE_RUN_KIND else (
        snapshot.backend if snapshot else (job or {}).get("backend", "unknown")
    )
    return {"directory": str(directory), "run_dir": str(run), "backend": backend,
            "machine": snapshot.machine_id if snapshot else "half", "status": (job or manifest or {}).get("status", "inputs"),
            "snapshot": read_runtime_state(snapshot_path) if snapshot else None,
            "snapshot_path": str(snapshot_path) if snapshot else None,
            "request": request, "result": result, "manifest": manifest, "job": job,
            "particle_evidence_path": str(run / EVIDENCE_FILE) if (run / EVIDENCE_FILE).is_file() else None,
            "real_evidence_path": str(real_path) if real_evidence else None,
            "image_index": images, "image_index_path": str(image_path) if images else None}


class ShadowJobController(QObject):
    message = pyqtSignal(str)
    progress = pyqtSignal(str)
    state_changed = pyqtSignal(str)
    completed = pyqtSignal(object)
    follow_update = pyqtSignal(object)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.process = None
        self.job_dir = None
        self.record = None
        self._stopping = False
        self._cleaned = False
        self._pid = 0
        self._progress_fragment = b""
        self._follow_fragment = b""
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._force_stop)

    @property
    def busy(self):
        return self.process is not None

    def start(self, request, output_root, *, mode, snapshot_path=None, evidence_path=None, runtime_json=None,
              ready_timeout=10.0, capture_timeout=5.0, save_images=False, image_limit_gib=10.0, image_backgrounds=None,
              capture_source=None, beam_path=None, handoff_path=None, model_timeout=120.0):
        if self.busy:
            raise ValueError("A shadow task is already running")
        particle_capture = mode in {CAPTURE_COMMAND, FOLLOW_COMMAND}
        if mode not in {"replay", "run-vm", "demo-real", "run-real", "run-model", CAPTURE_COMMAND, FOLLOW_COMMAND}:
            raise ValueError("Choose a supported Digital Shadow mode")
        if particle_capture and capture_source not in CAPTURE_SOURCES:
            raise ValueError("Choose VM or simulated Real capture")
        if mode == FOLLOW_COMMAND and (not beam_path or not handoff_path):
            raise ValueError("Follow requires saved six-dimensional particles and the IMPACT-T handoff JSON")
        if mode == "run-model":
            request = ParticleModelRequest.from_dict(request.as_metadata(), base=Path.cwd())
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
        arguments = [mode, "--output-root", str(directory)]
        if particle_capture:
            arguments.extend(["--source", capture_source, "--capture-timeout", str(capture_timeout)])
            if capture_source == "vm" and runtime_json:
                arguments.extend(["--vm-runtime-json", str(Path(runtime_json).expanduser().resolve())])
            if mode == FOLLOW_COMMAND:
                arguments.extend(["--beam-path", str(Path(beam_path).expanduser().resolve()),
                    "--impact-handoff", str(Path(handoff_path).expanduser().resolve()), "--model-timeout", str(model_timeout)])
        else:
            write_runtime_state(directory / "request.json", request.as_metadata())
            arguments.extend(["--request", str(directory / "request.json")])
        if mode == "replay":
            write_runtime_state(directory / "machine_snapshot.json", snapshot_to_dict(snapshot))
            image_path = Path(snapshot_path).resolve().parent / "image_index.json"
            if image_path.exists():
                write_runtime_state(directory / "image_index.json", read_object(image_path))
            arguments.extend(["--snapshot", str(directory / "machine_snapshot.json")])
            if evidence_path:
                name = "real_capture_evidence.json" if snapshot.backend == "real" else "vm_capture_evidence.json"
                write_runtime_state(directory / name, evidence)
                arguments.extend(["--real-evidence" if snapshot.backend == "real" else "--vm-evidence", str(directory / name)])
        elif mode != "run-model" and not particle_capture:
            arguments.extend(["--machine", "half", "--capture-timeout", str(capture_timeout)])
            if save_images:
                arguments.extend(["--save-images", "--image-limit-gib", str(image_limit_gib),
                                  "--image-store", str(Path(output_root).resolve() / "image_store")])
                if image_backgrounds:
                    arguments.extend(["--image-backgrounds", str(Path(image_backgrounds).resolve())])
            if mode == "run-real":
                arguments.append("--connect-real")
            if mode == "run-vm":
                arguments.extend(["--ready-timeout", str(ready_timeout)])
            if runtime_json and mode == "run-vm":
                arguments.extend(["--vm-runtime-json", str(Path(runtime_json).resolve())])
        self.job_dir = directory
        backend = {"run-model": "offline", "run-real": "real", "demo-real": "real", "run-vm": "vm"}.get(mode)
        if mode == "replay":
            backend = snapshot.backend
        if particle_capture:
            backend = capture_source
        self.record = {"schema": "digital_shadow_gui_job_v1", "status": "running", "mode": mode, "backend": backend,
                       "started_at": datetime.now(timezone.utc).isoformat(), "run_dir": None}
        if mode == "run-model":
            self.record["scope"] = request.scope
        self._save()
        self._stopping = self._cleaned = False
        self._pid = 0
        self._progress_fragment = b""
        self._follow_fragment = b""
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
        if data and stderr and self.record.get("scope") in ("preinjector", "coupled") and not self._stopping:
            *lines, self._progress_fragment = (self._progress_fragment + data).split(b"\n")
            prefix = PROGRESS_PREFIX.encode()
            for line in lines:
                if line.startswith(prefix):
                    self.progress.emit(line[len(prefix):].decode("utf-8", errors="replace"))
        if data and not stderr and self.record.get("mode") == FOLLOW_COMMAND and not self._stopping:
            *lines, self._follow_fragment = (self._follow_fragment + data).split(b"\n")
            for line in lines:
                self._read_follow_event(line)

    def _read_follow_event(self, line):
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get("schema") != FOLLOW_EVENT:
                return
            if event.get("source") != self.record["backend"]:
                raise ValueError("Follow update has the wrong capture source")
            result = event.get("result")
            if result:
                directory = Path(result["run_dir"]).resolve()
                if not directory.is_relative_to(self.job_dir / "cycles"):
                    raise ValueError("Follow result is outside its session")
                if self.record.get("run_dir") != str(directory):
                    archive = read_archive(directory)
                    if archive["status"] != "complete" or archive["manifest"]["snapshot_id"] != result["snapshot_id"]:
                        raise ValueError("Follow result does not match its snapshot")
                    self.record["run_dir"] = str(directory)
                    self._save()
            self.follow_update.emit(event)
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.message.emit(f"Invalid Follow update: {error}\n")

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
        mode = self.record["mode"]
        stopped = self._stopping or (mode in {CAPTURE_COMMAND, FOLLOW_COMMAND} and code == 130)
        self.record.update(status="interrupted" if stopped else "failed", exit_code=code,
                           finished_at=datetime.now(timezone.utc).isoformat())
        if mode == CAPTURE_COMMAND and not stopped and code == 0:
            captures = list(self.job_dir.glob("capture_*/machine_snapshot.json"))
            if len(captures) == 1 and (captures[0].parent / EVIDENCE_FILE).is_file():
                self.record.update(run_dir=str(captures[0].parent), status="complete")
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
