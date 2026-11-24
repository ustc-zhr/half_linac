"""Standalone HALF particle tracking from explicitly frozen model inputs."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from math import isclose
import os
from pathlib import Path
import shutil
import uuid

from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.elegant_backend.beam_sdds import write_elegant_beam
from half_linac.src.shared.elegant_backend.parser import _load_watch_rms_from_sdds, _new_legacy_sdds_dataset
from half_linac.src.shared.elegant_runtime import run_elegant_input
from half_linac.src.shared.machine_profile.model_snapshot import (
    build_model_snapshot_from_machine_state, save_model_snapshot,
)
from half_linac.src.shared.machine_profile.models import ControlBackendConfig
from half_linac.src.shared.machine_state import load_snapshot, snapshot_to_dict
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state
from half_linac.src.virtual_machine.beam_source import (
    BUNCHED_BEAM, SDDS_BEAM, apply_preferred_beam_source, normalize_beam_source_config,
    reference_momentum_key, unquote_elegant_string,
)

from .beam_state import C_M_S, BeamBoundary, BeamParticles, load_beam_state_archive, save_beam_state
from .impact_model import IMPACT_NOMINAL, IMPACT_VARIANT_LABELS
from .models import positive, text, timestamp
from .real_capture import normalized_real_input
from .session import digest, file_digest


PARTICLE_REQUEST_SCHEMA = "digital_shadow_particle_request_v1"
PARTICLE_RUN_KIND = "particle_model"
MODEL_LINE = "ALL_MAIN"
DEFAULT_IMPACT_TIMEOUT_S = 7200.0
DEFAULT_COUPLED_ELEGANT_TIMEOUT_S = 300.0
PROGRESS_PREFIX = "Particle model: "
SOURCE_LABELS = {
    "generated": "Generated bunch",
    "sdds": "SDDS particle file",
    "archived": "Saved six-dimensional particles",
}


@dataclass(frozen=True)
class ParticleModelRequest:
    scope: str = "downstream"
    beam_source: str = "generated"
    beam_path: str | None = None
    parameter_source: str = "design"
    snapshot_path: str | None = None
    model_timeout_s: float | None = None
    handoff_path: str | None = None
    impact_benchmark_dir: str | None = None
    impact_library_dir: str | None = None
    elegant_timeout_s: float | None = None
    impact_variant: str = IMPACT_NOMINAL
    capture_evidence_path: str | None = None

    def __post_init__(self):
        if self.scope not in ("downstream", "preinjector", "coupled"):
            raise ValueError("Choose a particle model scope")
        if self.beam_source not in SOURCE_LABELS:
            raise ValueError("Choose a particle source")
        if self.parameter_source not in ("design", "snapshot"):
            raise ValueError("Choose design settings or a saved snapshot")
        if self.impact_variant not in tuple(IMPACT_VARIANT_LABELS):
            raise ValueError("Choose a supported IMPACT-T working point")
        if self.scope in ("preinjector", "coupled"):
            text(self.impact_benchmark_dir, "P0 benchmark directory")
            text(self.impact_library_dir, "IMPACT-T library directory")
            if self.beam_source != "generated" or self.beam_path is not None or self.handoff_path is not None:
                raise ValueError("IMPACT-T tracking uses the P0 benchmark inputs")
            if self.scope == "preinjector" and self.parameter_source != "design":
                raise ValueError("Pre-injector tracking uses the P0 design settings")
        elif (self.impact_benchmark_dir is not None or self.impact_library_dir is not None
              or self.impact_variant != IMPACT_NOMINAL):
            raise ValueError("IMPACT-T settings require an IMPACT-T scope")
        if self.scope == "coupled":
            if self.parameter_source != "snapshot":
                raise ValueError("Coupled tracking requires a saved VM snapshot")
            elegant_timeout = DEFAULT_COUPLED_ELEGANT_TIMEOUT_S if self.elegant_timeout_s is None else self.elegant_timeout_s
            object.__setattr__(self, "elegant_timeout_s", positive(elegant_timeout, "ELEGANT timeout"))
        elif self.elegant_timeout_s is not None:
            raise ValueError("A separate ELEGANT timeout is available in coupled mode")
        if self.scope == "downstream":
            if self.beam_source != "generated":
                text(self.beam_path, "Particle input file")
            elif self.beam_path is not None:
                raise ValueError("A generated bunch uses the tracking template's beam settings")
        if self.parameter_source == "snapshot":
            text(self.snapshot_path, "Snapshot JSON")
        elif self.snapshot_path is not None:
            raise ValueError("Select saved snapshot parameters to use a snapshot")
        if self.capture_evidence_path is not None:
            text(self.capture_evidence_path, "Particle capture evidence JSON")
            if self.scope != "downstream" or self.parameter_source != "snapshot":
                raise ValueError("Particle capture evidence requires downstream tracking with a saved snapshot")
        if self.handoff_path is not None:
            text(self.handoff_path, "IMPACT-T handoff JSON")
            if (self.scope, self.beam_source, self.parameter_source) != ("downstream", "archived", "snapshot"):
                raise ValueError("An IMPACT-T handoff requires downstream tracking with archived particles and a VM snapshot")
        timeout = self.model_timeout_s
        if timeout is None:
            timeout = 120.0 if self.scope == "downstream" else DEFAULT_IMPACT_TIMEOUT_S
        object.__setattr__(self, "model_timeout_s", positive(timeout, "Model timeout"))

    @classmethod
    def from_dict(cls, payload, *, base):
        if not isinstance(payload, dict) or payload.get("schema") != PARTICLE_REQUEST_SCHEMA:
            raise ValueError("Unsupported particle model request schema")
        fields = {key: value for key, value in payload.items() if key != "schema"}
        for key in ("beam_path", "snapshot_path", "handoff_path", "impact_benchmark_dir", "impact_library_dir",
                    "capture_evidence_path"):
            if fields.get(key) is not None:
                value = Path(text(fields[key], key)).expanduser()
                fields[key] = str((Path(base) / value).resolve())
        return cls(**fields)

    def as_metadata(self):
        fields = asdict(self)
        for key in ("handoff_path", "impact_benchmark_dir", "impact_library_dir", "elegant_timeout_s",
                    "capture_evidence_path"):
            if fields[key] is None:
                del fields[key]
        if self.scope == "downstream":
            del fields["impact_variant"]
        return {"schema": PARTICLE_REQUEST_SCHEMA, **fields}


def quadrupole_fields(state):
    return tuple((name, "K1") for name in dict.fromkeys(state["usedline"])
                 if state["lattice"][name]["TYPE"].upper() == "QUAD")


def _apply_saved_quadrupoles(context, state, snapshot, directory):
    if snapshot.backend not in ("vm", "real"):
        raise ValueError("Choose a saved HALF VM or real machine snapshot")
    write_runtime_state(directory / "machine_snapshot.json", snapshot_to_dict(snapshot))
    fields = quadrupole_fields(state)
    start, end = timestamp(snapshot.capture_started_at), timestamp(snapshot.capture_finished_at)
    if end < start:
        raise ValueError("Invalid snapshot capture window")
    channel = "K1_total" if snapshot.backend == "real" else "K1"
    input_snapshot, normalizations = (
        normalized_real_input(snapshot, fields) if snapshot.backend == "real" else (snapshot, [])
    )
    context = replace(context, control_backend=ControlBackendConfig(snapshot.backend))
    model_snapshot = build_model_snapshot_from_machine_state(
        context, input_snapshot, fields, logical_channels=dict.fromkeys(fields, channel),
    )
    entries = {item.key: item for item in snapshot.entries}
    overrides = model_snapshot.lattice_overrides
    for element, _ in fields:
        entry = entries[f"{element}/{channel}"]
        if entry.received_at is None or not start <= timestamp(entry.received_at) <= end:
            raise ValueError(f"{entry.key} was not received within the snapshot capture window")
        state["lattice"][element]["K1"] = str(overrides[element]["K1"])
    save_model_snapshot(model_snapshot, directory / "model_snapshot.json", extra_metadata={
        "machine_snapshot_id": snapshot.snapshot_id, "unit_normalizations": normalizations,
    })
    return snapshot.snapshot_id


def _read_impact_handoff(path, beam):
    handoff = read_runtime_state(path)
    if not isinstance(handoff, dict):
        raise ValueError("Expected an IMPACT-T handoff metadata object")
    text(handoff["source"], "IMPACT-T source trajectory")
    low, high = handoff["gate_phase_deg"]
    drift = positive(handoff["drift_in_impact_m"], "Drift tracked in IMPACT-T")
    if handoff["extra_drift_m"] != 0 or not isclose(
        handoff["native_z_m"] - handoff["rf_end_z_m"], drift, rel_tol=0, abs_tol=1e-12,
    ):
        raise ValueError("The IMPACT-T native plane must include the RF exit drift and use zero extra drift")
    frequency = positive(handoff["frequency_Hz"], "IMPACT-T gate frequency")
    phase = beam.payload.coordinates[:, 4] * (360 * frequency / C_M_S)
    if (
        handoff["selected_particles"] != len(phase)
        or handoff["p_ref_MeV_c"] != beam.reference.p_ref_mev_c
        or not isclose(handoff["selected_charge_pC"], abs(beam.bunch_charge_c) * 1e12, rel_tol=1e-12)
        or handoff["gate_clock_s"] != beam.reference.t_ref_s
        or frequency not in (rf.frequency_hz for rf in beam.reference.rf)
        or not ((low < phase) & (phase < high)).all()
    ):
        raise ValueError("IMPACT-T handoff metadata disagrees with the frozen particle archive")
    return handoff


def _stage_beam(request, state, boundary, inputs, workdir, manifest):
    control = state["control"]
    setup = control["run_setup"]
    reference_key = reference_momentum_key(control)
    archived_path = None
    if request.beam_source == "generated":
        config = {"selected": BUNCHED_BEAM, BUNCHED_BEAM: control.get(BUNCHED_BEAM),
                  "twiss_output": control.get("twiss_output"), "run_setup": setup}
    else:
        particle_file = inputs / "input.sdds"
        if request.beam_source == "archived":
            beam, record = load_beam_state_archive(request.beam_path)
            if beam.boundary != boundary:
                raise ValueError("Saved beam does not match the ELEGANT model entrance (line, lattice and edge)")
            if not isinstance(beam.payload, BeamParticles):
                raise ValueError("Select saved particles; six-dimensional moments alone cannot be tracked")
            if beam.bunch_charge_c is None:
                raise ValueError("Saved beam requires an explicit bunch charge")
            if request.handoff_path:
                handoff = _read_impact_handoff(request.handoff_path, beam)
                write_runtime_state(inputs / "impact-handoff.json", handoff)
                manifest["impact_handoff"] = handoff
            archived_path = save_beam_state(beam, inputs / "entrance.json")
            write_elegant_beam(beam, particle_file)
            manifest["particle_input"] = {
                "source_npz_sha256": record["data"]["sha256"],
                "sdds_sha256": file_digest(particle_file),
                "count": len(beam.payload.coordinates),
                "charge_c": beam.bunch_charge_c,
                "reference_momentum_mev_c": beam.reference.p_ref_mev_c,
            }
            reference_key = "p_central_mev"
            setup = {reference_key: str(beam.reference.p_ref_mev_c)}
            control["run_setup"]["always_change_p0"] = "0"
            state["lattice"][boundary.element]["TOTAL"] = str(abs(beam.bunch_charge_c))
        else:
            shutil.copyfile(request.beam_path, particle_file)
            archived_path = particle_file
        config = {"selected": SDDS_BEAM, "run_setup": setup, SDDS_BEAM: {
            "input": str(particle_file), "sample_interval": "1", "center_arrival_time": "0",
            "reuse_bunch": "0", "center_transversely": "0", "reverse_t_sign": "0", "sample_fraction": "1",
        }}
    normalized = normalize_beam_source_config(config, elegant_dir=workdir, reference_key=reference_key)
    apply_preferred_beam_source(control, normalized)
    if normalized["selected"] == SDDS_BEAM:
        # Known relative path keeps input deck portable and avoids quoting an arbitrary user filename.
        control[SDDS_BEAM]["input"] = '"../inputs/input.sdds"'
    return archived_path


def _stage_resources(state, source_directory, workdir, *, environment=None):
    staged = {}
    for element_id in dict.fromkeys(state["usedline"]):
        for key, value in state["lattice"][element_id].items():
            if key not in ("ZWAKEFILE", "TRWAKEFILE", "WAKEFILE"):
                continue
            source = (source_directory / unquote_elegant_string(value)).resolve()
            if source not in staged:
                staged[source] = f"wake-{len(staged)}.sdds"
                shutil.copyfile(source, workdir / staged[source])
            state["lattice"][element_id][key] = staged[source]
    environment = os.environ.copy() if environment is None else environment.copy()
    if environment.get("RPN_DEFNS"):
        rpn_file = workdir / "defns.rpn"
        shutil.copyfile(Path(environment["RPN_DEFNS"]).expanduser(), rpn_file)
        environment["RPN_DEFNS"] = str(rpn_file)
    return environment


def _screen_outputs(profile, state):
    files = {}
    for element in profile.elements:
        if element.kind != "flag" or state["usedline"].count(element.id) != 1:
            continue
        spec = state["lattice"][element.id]
        if spec["TYPE"].upper() == "WATCH" and spec.get("DISABLE", "0") == "0" and (
            unquote_elegant_string(spec.get("MODE", "")).lower() in ("coord", "coordinate", "coordinates")
        ):
            output = f"screen-{len(files)}.sdds"
            files[element.id] = output
            spec["FILENAME"] = output
    if not files:
        raise ValueError("The downstream line has no enabled particle screen diagnostics")
    return files


def _read_predictions(files, workdir):
    predictions = []
    for element, filename in files.items():
        try:
            path = workdir / filename
            if not path.is_file() or not path.stat().st_size:
                raise ValueError(f"No particle output for {element}")
            widths = _load_watch_rms_from_sdds(path)
            status, detail = "predicted", ""
        except (RuntimeError, ValueError, OSError) as error:
            widths = (None, None)
            status, detail = "unavailable", str(error)
        for axis, width in zip(("sigx", "sigy"), widths):
            predictions.append({"key": f"{element}/{axis}", "predicted": width, "unit": "mm",
                                "width_definition": "rms", "status": status, "detail": detail})
    if all(row["predicted"] is None for row in predictions):
        raise ValueError("No particle screen prediction is available: " + predictions[0]["detail"])
    return predictions


def run_particle_model(context, request: ParticleModelRequest, output_root, *, progress=None):
    """Run a standalone model without reading or publishing EPICS values."""
    if request.scope == "preinjector":
        from .impact_model import run_preinjector

        return run_preinjector(request, output_root, progress=progress)
    if request.scope == "coupled":
        from .coupled_model import run_coupled

        return run_coupled(context, request, output_root, progress=progress)
    return run_downstream(context, request, Path(output_root).resolve() / f"shadow_{uuid.uuid4().hex}")


def downstream_sources(context):
    if context.machine.id != "half" or context.model_backend is None or context.model_backend.engine != "elegant":
        raise ValueError("Particle model requires a HALF ELEGANT context")
    config = context.model_backend.config
    return Path(config["source_lattice"]), Path(config["tracking_ini_ele"])


def downstream_state(lattice, ele):
    parser = ElegantParser(lattice, ele, MODEL_LINE)
    state = parser.build_runtime_state()
    if state["usedline"][:1] != ["C"] or state["lattice"]["C"]["TYPE"].upper() != "CHARGE":
        raise ValueError("The HALF tracking model must use C#1 entrance as its input plane")
    return parser, state, BeamBoundary("ELEGANT_ENTRANCE", "half", file_digest(lattice), MODEL_LINE, "C", 1, "entrance")


def end_particles(path):
    if not path.is_file():
        raise ValueError(f"Missing ELEGANT centroid output: {path}")
    dataset = _new_legacy_sdds_dataset()
    dataset.load(str(path))
    if "Particles" not in dataset.columnName:
        raise ValueError(f"ELEGANT centroid output has no Particles column: {path}")
    pages = dataset.columnData[dataset.columnName.index("Particles")]
    if not len(pages) or not len(pages[-1]):
        raise ValueError(f"ELEGANT centroid output has no particle counts: {path}")
    return int(pages[-1][-1])


def run_downstream(context, request, directory, *, environment=None, stop_requested=None):
    inputs, workdir = directory / "inputs", directory / "elegant"
    inputs.mkdir(parents=True, exist_ok=False)
    workdir.mkdir()
    manifest_path = directory / "manifest.json"
    manifest = {"schema": "digital_shadow_run_v1", "kind": PARTICLE_RUN_KIND, "run_id": directory.name,
                "scope": request.scope, "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    write_runtime_state(manifest_path, manifest)
    try:
        write_runtime_state(directory / "request.json", request.as_metadata())
        source, template = downstream_sources(context)
        lattice, ele = inputs / "source.lte", inputs / "tracking-template.ele"
        shutil.copyfile(source, lattice)
        shutil.copyfile(template, ele)
        parser, state, boundary = downstream_state(lattice, ele)
        manifest.update(boundary=asdict(boundary), beam_source=request.beam_source,
                        parameter_source=request.parameter_source)
        if request.beam_path:
            manifest["beam_file"] = Path(request.beam_path).name
        if request.snapshot_path:
            snapshot = load_snapshot(request.snapshot_path)
            synthetic = False
            if request.capture_evidence_path:
                from .particle_capture import EVIDENCE_FILE, verify_particle_capture

                evidence = read_runtime_state(request.capture_evidence_path)
                verify_particle_capture(context, state, snapshot, evidence)
                synthetic = evidence["source"] == "demo-real"
                write_runtime_state(directory / EVIDENCE_FILE, evidence)
                manifest["capture_source"] = evidence["source"]
            if request.handoff_path and snapshot.backend != "vm" and not synthetic:
                raise ValueError("An IMPACT-T handoff requires a saved VM snapshot")
            manifest["snapshot_id"] = _apply_saved_quadrupoles(context, state, snapshot, directory)
        beam_path = _stage_beam(request, state, boundary, inputs, workdir, manifest)
        environment = _stage_resources(state, source.parent, workdir, environment=environment)
        files = _screen_outputs(context.profile, state)
        control = state["control"]
        control["run_control"]["n_steps"] = "1"
        state_path = inputs / "evaluated_state.json"
        write_runtime_state(state_path, state)
        parser.json_to_lte_ele(workdir / "tracking.lte", workdir / "tracking.ele", state_path)
        archived_request = replace(request,
            beam_path=str(beam_path.relative_to(directory)) if beam_path else None,
            snapshot_path="machine_snapshot.json" if request.snapshot_path else None,
            capture_evidence_path=EVIDENCE_FILE if request.capture_evidence_path else None,
            handoff_path="inputs/impact-handoff.json" if request.handoff_path else None)
        write_runtime_state(directory / "request.json", archived_request.as_metadata())
        executable = shutil.which("elegant")
        if executable is None:
            raise RuntimeError("elegant executable is not available in PATH")
        manifest["engine"] = {"executable": executable, "sha256": file_digest(executable)}
        manifest["input_hash"] = digest({
            "files": _artifacts(directory), "engine": manifest["engine"],
            "implementation": file_digest(__file__),
        })
        write_runtime_state(manifest_path, manifest)
        cancellation = {} if stop_requested is None else {"stop_requested": stop_requested}
        run_elegant_input("tracking.ele", "tracking.log", workdir=workdir,
                          timeout_s=request.model_timeout_s, env=environment, **cancellation)
        predictions = _read_predictions(files, workdir)
        if stop_requested is not None and stop_requested():
            raise KeyboardInterrupt
        status = "complete" if all(row["status"] == "predicted" for row in predictions) else "partial"
        result = {"kind": PARTICLE_RUN_KIND, "run_id": directory.name, "status": status,
                  "input_hash": manifest["input_hash"], "predictions": predictions, "comparisons": []}
        if request.capture_evidence_path:
            result["end_particles"] = end_particles(workdir / "tracking.cen")
        write_runtime_state(directory / "result.json", result)
        manifest["status"] = status
    except KeyboardInterrupt:
        (directory / "result.json").unlink(missing_ok=True)
        manifest.update(status="interrupted", error={"message": "ELEGANT tracking interrupted"})
        raise
    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as error:
        manifest.update(status="failed", error={"type": type(error).__name__, "message": str(error)})
        raise RuntimeError(f"Particle model failed: {error}; see {manifest_path}") from error
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["artifacts"] = _artifacts(directory)
        write_runtime_state(manifest_path, manifest)
    return {**result, "run_dir": str(directory)}


def _artifacts(directory):
    return {path.relative_to(directory).as_posix(): file_digest(path)
            for path in sorted(directory.rglob("*")) if path.is_file() and path != directory / "manifest.json"
            and not any(part.startswith(".") for part in path.relative_to(directory).parts)}
