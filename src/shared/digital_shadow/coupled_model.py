"""Run the HALF P0 injector and ELEGANT tracking in one offline archive."""

from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import uuid

from half_linac.src.shared.machine_state import load_snapshot
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state

from .impact_model import WORKING_POINT, load_impact_reference, run_preinjector
from .particle_model import (
    PARTICLE_RUN_KIND, ParticleModelRequest, _apply_saved_quadrupoles, downstream_sources, downstream_state,
    end_particles as _end_particles, run_downstream,
)
from .session import digest, file_digest


def _prepare(context, request, directory):
    if shutil.which("elegant") is None:
        raise ValueError("elegant executable is not available in PATH")
    rpn = os.environ.get("RPN_DEFNS")
    if rpn and not Path(rpn).expanduser().is_file():
        raise ValueError(f"ELEGANT RPN definitions are unavailable: {rpn}")
    snapshot = load_snapshot(request.snapshot_path)
    if snapshot.machine_id != "half" or snapshot.backend != "vm":
        raise ValueError("Coupled tracking requires a saved HALF VM snapshot")
    lattice, template = downstream_sources(context)
    _, state, boundary = downstream_state(lattice, template)
    _, frozen_beam = load_impact_reference(Path(request.impact_benchmark_dir), read_runtime_state(WORKING_POINT))
    if frozen_beam.boundary != boundary:
        raise ValueError("The P0 handoff does not match the current ELEGANT lattice entrance")
    _apply_saved_quadrupoles(context, state, snapshot, directory)
    return snapshot.snapshot_id


def run_coupled(context, request, output_root, *, progress=None):
    directory = Path(output_root).resolve() / f"shadow_{uuid.uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=False)
    upstream, downstream = directory / "upstream", directory / "downstream"
    stages = {path.name: {"directory": path.name, "status": "pending"} for path in (upstream, downstream)}
    manifest = {"schema": "digital_shadow_run_v1", "kind": PARTICLE_RUN_KIND, "run_id": directory.name,
                "scope": "coupled", "impact_variant": request.impact_variant, "status": "running", "stages": stages,
                "started_at": datetime.now(timezone.utc).isoformat()}
    manifest_path = directory / "manifest.json"
    request_path = directory / "request.json"
    result_path = directory / "result.json"
    write_runtime_state(manifest_path, manifest)
    active = None
    if progress is None:
        progress = lambda _message: None

    try:
        write_runtime_state(request_path, request.as_metadata())
        progress("Preparing saved VM snapshot and P0 handoff")
        manifest["snapshot_id"] = _prepare(context, request, directory)
        snapshot_path = directory / "machine_snapshot.json"
        snapshot_sha256 = file_digest(snapshot_path)
        archived_request = replace(request, snapshot_path=snapshot_path.name)
        write_runtime_state(request_path, archived_request.as_metadata())
        upstream_request = ParticleModelRequest(scope="preinjector", impact_benchmark_dir=request.impact_benchmark_dir,
            impact_library_dir=request.impact_library_dir, model_timeout_s=request.model_timeout_s,
            impact_variant=request.impact_variant)
        active = stages["upstream"]
        active["status"] = "running"
        write_runtime_state(manifest_path, manifest)
        beam_result = run_preinjector(upstream_request, directory, directory=upstream,
                                      progress=lambda message: progress(f"IMPACT-T · {message}"))
        if beam_result["status"] == "interrupted":
            raise KeyboardInterrupt
        active["status"] = "complete"
        active = None
        handoff_path = upstream / beam_result["handoff_path"]
        manifest["impact_handoff"] = read_runtime_state(handoff_path)
        manifest["beam_statistics"] = beam_result["beam_statistics"]
        manifest["deck_sha256"] = beam_result["deck_sha256"]
        if file_digest(snapshot_path) != snapshot_sha256:
            raise ValueError("Saved VM snapshot changed inside the coupled archive")

        active = stages["downstream"]
        active["status"] = "running"
        write_runtime_state(manifest_path, manifest)
        progress("ELEGANT · Tracking ALL_MAIN with the saved VM snapshot")
        downstream_request = ParticleModelRequest(beam_source="archived",
            beam_path=str(upstream / beam_result["beam_path"]), parameter_source="snapshot",
            snapshot_path=str(snapshot_path), handoff_path=str(handoff_path), model_timeout_s=request.elegant_timeout_s)
        tracked = run_downstream(context, downstream_request, downstream)
        active["status"] = tracked["status"]
        end_particles = _end_particles(downstream / "elegant/tracking.cen")
        active = None
        manifest["input_hash"] = digest({"request": archived_request.as_metadata(), "snapshot_sha256": snapshot_sha256,
            "upstream": beam_result["input_hash"], "downstream": tracked["input_hash"]})
        result = {"kind": PARTICLE_RUN_KIND, "scope": "coupled", "run_id": directory.name,
                  "status": tracked["status"], "input_hash": manifest["input_hash"],
                  "impact_variant": request.impact_variant, "deck_sha256": manifest["deck_sha256"],
                  "predictions": tracked["predictions"], "comparisons": [],
                  "beam_statistics": beam_result["beam_statistics"], "end_particles": end_particles}
        write_runtime_state(result_path, result)
        manifest["status"] = result["status"]
    except KeyboardInterrupt:
        if active is not None:
            active["status"] = "interrupted"
        result_path.unlink(missing_ok=True)
        manifest.update(status="interrupted", error={"message": "Coupled tracking interrupted"})
        return {"run_dir": str(directory), "status": "interrupted"}
    except (RuntimeError, ValueError, TypeError, KeyError, IndexError, OSError) as error:
        if active is not None:
            active["status"] = "failed"
        result_path.unlink(missing_ok=True)
        manifest.update(status="failed", error={"type": type(error).__name__, "message": str(error)})
        raise RuntimeError(f"Coupled model failed: {error}; see {manifest_path}") from error
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        names = ("request.json", "machine_snapshot.json", "model_snapshot.json", "result.json",
                 "upstream/manifest.json", "downstream/manifest.json")
        manifest["artifacts"] = {name: file_digest(directory / name) for name in names if (directory / name).is_file()}
        write_runtime_state(manifest_path, manifest)
    return {**result, "run_dir": str(directory)}
