"""One saved working point, one model calculation, one traceable comparison."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

import numpy as np
import sdds

from half_linac.src.shared.machine_profile.model_snapshot import (
    build_model_snapshot_from_machine_state, save_model_snapshot,
)
from half_linac.src.shared.machine_state import snapshot_to_dict
from half_linac.src.shared.runtime_state import write_runtime_state

from .comparison import compare_diagnostics
from .models import timestamp
from .pipeline import prepare_model, predict


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_once(context, snapshot, request, output_root, *, vm_evidence=None):
    """Replay saved inputs. This entry point performs no EPICS reads or writes."""
    directory = Path(output_root).resolve() / f"shadow_{uuid.uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": "digital_shadow_run_v1", "run_id": directory.name,
                "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                "snapshot_id": snapshot.snapshot_id, "capture_consistency": snapshot.consistency,
                "beam_basis": request.beam_basis, "beam_provenance": request.beam_provenance,
                "mode": "offline_replay", "python": sys.version,
                "fidelity": "uncoupled linear transverse optics; no RF, bends, tracking, wake or loss prediction"}
    manifest_path = directory / "manifest.json"
    write_runtime_state(manifest_path, manifest)
    try:
        raw = snapshot_to_dict(snapshot)
        write_runtime_state(directory / "machine_snapshot.json", raw)
        write_runtime_state(directory / "request.json", request.as_metadata())
        diagnostic_window = None
        if vm_evidence is not None:
            from .vm_capture import verify_vm_evidence
            write_runtime_state(directory / "vm_capture_evidence.json", vm_evidence)
            diagnostic_window = verify_vm_evidence(context, snapshot, request, vm_evidence)
            manifest["mode"] = "vm_capture_replay"
            manifest["vm_input_version"] = vm_evidence["status"]["input_version"]
        started, finished = timestamp(snapshot.capture_started_at), timestamp(snapshot.capture_finished_at)
        if not 0 <= finished - started <= request.max_capture_duration_s:
            raise ValueError("Capture duration exceeds the declared working-point window")
        if request.model_backend != context.model_backend.name:
            raise ValueError("Request and context model backends differ")
        state, fields = prepare_model(context, request)
        channels = {field: "K1_total" if snapshot.backend == "real" else "K1" for field in fields}
        model_snapshot = build_model_snapshot_from_machine_state(context, snapshot, fields, logical_channels=channels)
        entries = {entry.key: entry for entry in snapshot.entries}
        for element, field in fields:
            entry = entries[f"{element}/{channels[(element, field)]}"]
            if entry.received_at is None or not started <= timestamp(entry.received_at) <= finished:
                raise ValueError(f"{entry.key} was not received within the capture window")
        manifest["snapshot_hash"] = digest(raw)
        executable = shutil.which("elegant")
        manifest["engine"] = {"name": "elegant", "executable": executable,
                              "sha256": file_digest(executable) if executable else None}
        manifest["dependencies"] = {"numpy": np.__version__, "sdds_module": sdds.__file__,
                                    "sdds_module_sha256": file_digest(sdds.__file__)}
        shared = Path(__file__).resolve().parents[1]
        manifest["implementation"] = {str(path.relative_to(shared)): file_digest(path)
                                      for path in (*sorted(Path(__file__).parent.glob("*.py")),
                                                   shared / "machine_profile/model_backend.py",
                                                   shared / "machine_profile/model_snapshot.py",
                                                   shared / "elegant_backend/parser.py",
                                                   shared / "elegant_runtime.py", shared / "beam_matrix.py")}
        manifest["input_hash"] = digest({"capture": raw, "request": request.as_metadata(),
                                         "model": state, "engine": manifest["engine"],
                                         "implementation": manifest["implementation"], "dependencies": manifest["dependencies"],
                                         "model_snapshot": model_snapshot.as_metadata(), "vm_evidence": vm_evidence})
        save_model_snapshot(model_snapshot, directory / "model_snapshot.json", extra_metadata={
            "machine_snapshot_id": snapshot.snapshot_id, "machine_snapshot_hash": manifest["snapshot_hash"],
            "strength_basis": "real K1_total readback; VM K1; units 1/m^2",
        })
        predictions, matrices = predict(context, request, state, model_snapshot, directory)
        comparisons = compare_diagnostics(context, snapshot, request, predictions, diagnostic_window=diagnostic_window)
        status = "complete" if all(row["status"] == "compared" for row in comparisons) else "partial"
        result = {"run_id": directory.name, "input_hash": manifest["input_hash"], "status": status,
                  "beam_basis": request.beam_basis, "beam_provenance": request.beam_provenance,
                  "comparisons": comparisons, "transfer_matrices": matrices}
        write_runtime_state(directory / "result.json", result)
        manifest["status"] = status
    except Exception as exc:
        manifest.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        raise RuntimeError(f"Shadow run failed: {exc}; see {manifest_path}") from exc
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["artifacts"] = {str(path.relative_to(directory)): file_digest(path)
                                 for path in sorted(directory.rglob("*"))
                                 if path.is_file() and path != manifest_path
                                 and not any(part.startswith(".") for part in path.relative_to(directory).parts)}
        write_runtime_state(manifest_path, manifest)
    return {**result, "run_dir": str(directory)}
