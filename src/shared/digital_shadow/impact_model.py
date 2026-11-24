"""Run the saved HALF P0 IMPACT-T working point and export its native plane."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import re
import shutil
import subprocess
from time import monotonic
import uuid

import numpy as np

from half_linac.src.shared.beam_matrix import ELECTRON_REST_ENERGY_MEV as MASS_MEV
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state

from .beam_state import C_M_S, BeamParticles, load_beam_state, save_beam_state
from .session import digest, file_digest


WORKING_POINT = Path(__file__).resolve().parents[3] / "configs/machines/half/benchmarks/digital_shadow_p1.json"
DECK = Path("ImpactT.in")
IMPACT_NOMINAL = "nominal"
IMPACT_L01B_PROBE = "l01b-amplitude-plus1pct"
IMPACT_VARIANT_LABELS = {
    IMPACT_NOMINAL: "P0 nominal working point",
    IMPACT_L01B_PROBE: "P1 · L01B field amplitude +1%",
}
_STEP = re.compile(r"^\s*i,t,<z>:\s*(\d+)\s+([\d.eEdD+-]+)", re.MULTILINE)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _input_checksums(manifest, working_point):
    _require(file_digest(manifest) == working_point["inputs_manifest_sha256"],
             "IMPACT-T input manifest differs from the working point")
    return {Path(path).relative_to("input"): sha for sha, path in
            (line.split(maxsplit=1) for line in manifest.read_text().splitlines())}


def verify_impact_inputs(directory, working_point, *, expected_deck=None):
    impact = directory / "impact"
    _require(file_digest(directory / "ImpactTexe") == working_point["engine_sha256"],
             f"{directory.name} IMPACT-T executable differs from the working point")
    for name, expected in _input_checksums(directory / "inputs.sha256", working_point).items():
        if name == DECK and expected_deck is not None:
            _require((impact / name).read_text() == expected_deck,
                     "Probe deck must differ only in the L01B RF amplitude column")
        else:
            _require(file_digest(impact / name) == expected, f"{directory.name} input checksum differs for {name}")


def l01b_probe_deck(text, probe):
    lines = text.splitlines(keepends=True)
    start, end = Decimal(probe["start_m"]), Decimal(probe["end_m"])
    factor = Decimal(probe["amplitude_scale"])
    count = 0
    for index in range(9, len(lines)):
        tokens = list(re.finditer(r"\S+", lines[index]))
        values = [token.group() for token in tokens]
        if len(values) < 5 or values[3] != "112" or not start <= Decimal(values[4]) < end:
            continue
        _require(len(values) >= 9 and int(values[6]) == probe["frequency_hz"] and int(values[8]) in probe["field_map_ids"],
                 f"Unexpected L01B RF card at line {index + 1}")
        lo, hi = tokens[5].span()
        amplitude = format((Decimal(values[5]) * factor).normalize(), "f")
        lines[index] = lines[index][:lo] + amplitude + lines[index][hi:]
        count += 1
    _require(count == probe["cards"], f"Expected {probe['cards']} L01B RF cards, found {count}")
    return "".join(lines)


def stage_impact(benchmark, directory, working_point, *, variant=IMPACT_NOMINAL):
    _require(variant in IMPACT_VARIANT_LABELS, "Choose a supported IMPACT-T working point")
    source = benchmark / "impact-t"
    impact = directory / "impact"
    impact.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(source / "inputs.sha256", directory / "inputs.sha256")
    for name in _input_checksums(directory / "inputs.sha256", working_point):
        shutil.copyfile(source / "input" / name, impact / name)
    shutil.copy2(source / "engine/ImpactTexe", directory / "ImpactTexe")
    verify_impact_inputs(directory, working_point)
    if variant == IMPACT_L01B_PROBE:
        deck = impact / DECK
        deck.write_text(l01b_probe_deck(deck.read_text(), working_point["l01b_probe"]))
    return impact


def _wait_for_impact(process, log_path, deadline, progress):
    pending = ""
    with log_path.open(encoding="utf-8", errors="replace") as log:
        while True:
            remaining = deadline - monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise TimeoutError(f"IMPACT-T exceeded its tracking timeout. See {log_path}")
            try:
                return process.wait(timeout=min(1.0, remaining) if remaining is not None else 1.0)
            except subprocess.TimeoutExpired:
                if progress is not None:
                    lines, _, pending = (pending + log.read()).rpartition("\n")
                    steps = _STEP.findall(lines)
                    if steps:
                        step, time_s = steps[-1]
                        time_ns = float(time_s.lower().replace("d", "e")) * 1e9
                        progress(f"Tracking · step {step} · t = {time_ns:.3f} ns")


def run_impact(directory, library_dir, *, timeout_s=None, progress=None):
    impact = directory / "impact"
    _require(library_dir.is_dir(), f"IMPACT-T library directory does not exist: {library_dir}")
    engine = directory / "ImpactTexe"
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (str(library_dir), env.get("LD_LIBRARY_PATH"))))
    log_path = impact / "run.log"
    started = monotonic()
    deadline = started + timeout_s if timeout_s is not None else None
    with log_path.open("wb") as log:
        process = subprocess.Popen([engine], cwd=impact, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT)
        try:
            code = _wait_for_impact(process, log_path, deadline, progress)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            (impact / "exit-status.txt").write_text(f"{process.returncode}\n")
    write_runtime_state(directory / "timing.json", {"wall_s": monotonic() - started})
    _require(code == 0, f"IMPACT-T exited with {code}. See {log_path}")


def ensure_impact_finished(impact):
    log = impact / "run.log"
    with log.open(encoding="utf-8", errors="replace") as stream:
        complete = any("before  destruct4:" in line for line in stream)
    screens = impact / "screens.tsv"
    _require((impact / "exit-status.txt").read_text().strip() == "0" and complete,
             f"IMPACT-T did not finish successfully. See {log}")
    _require(screens.is_file() and screens.stat().st_size > 0, f"Missing IMPACT-T native-plane records: {screens}")


def beam_at_boundary(impact, handoff, frozen, detail, *, stage="P1 offline recomputation"):
    screen = handoff["physical_screen_id"]
    screen_map = read_runtime_state(impact / "screen-map.json")
    aperture = np.loadtxt(impact / "aperture-control.in", skiprows=1, ndmin=2)
    _require(screen_map[str(screen - 1)] == screen and aperture[screen - 1, 0] == handoff["native_z_m"],
             "IMPACT-T screen mapping differs from the native handoff plane")
    path = impact / "screens.tsv"
    raw = np.loadtxt(path, skiprows=1, ndmin=2)
    _require(raw.size and raw.shape[1] == 9, f"Invalid IMPACT-T native-plane records: {path}")
    raw = raw[(raw[:, 0] == screen) & (raw[:, 8] == 1) & (raw[:, 7] > 0)]
    chronological = raw[np.argsort(raw[:, 2], kind="stable")]
    ids, first = np.unique(chronological[:, 1].astype(np.uint64), return_index=True)
    rows = chronological[first]
    frequency, clock = handoff["frequency_Hz"], handoff["gate_clock_s"]
    low, high = handoff["gate_phase_deg"]
    phase = 360 * frequency * (rows[:, 2] - clock)
    selected = (phase > low) & (phase < high)
    _require(np.any(selected), "No forward particles in the native-plane phase gate")
    _, _, t, x, ux, y, uy, uz, _ = rows[selected].T
    u = np.sqrt(ux * ux + uy * uy + uz * uz)
    p_ref = float(u.mean() * MASS_MEV)
    u_ref = p_ref / MASS_MEV
    coordinates = np.column_stack((x, ux / u_ref, y, uy / u_ref, C_M_S * (t - clock), u / u_ref - 1))
    # Follow the P0 deck's I = Q f convention and retain its equal macrocharge.
    deck = (impact / DECK).read_text().splitlines()
    current, n_input = float(deck[8].split()[0]), int(deck[2].split()[1])
    macrocharge = current / frequency / n_input
    state = replace(frozen, payload=BeamParticles(coordinates, np.ones(len(t)), ids[selected]),
                    reference=replace(frozen.reference, p_ref_mev_c=p_ref),
                    bunch_charge_c=-len(t) * macrocharge,
                    provenance=replace(frozen.provenance, detail=detail, stage=stage))
    metadata = {**handoff, "source": str(path), "source_sha256": file_digest(path),
                "forward_particles": len(ids), "selected_particles": len(t),
                "per_macro_pC": macrocharge * 1e12, "selected_charge_pC": -state.bunch_charge_c * 1e12,
                "p_ref_MeV_c": p_ref, "t_min_ns": float(t.min() * 1e9), "t_max_ns": float(t.max() * 1e9)}
    return state, metadata


def beam_stats(state):
    x, ux, y, uy, tau, delta = state.payload.coordinates.T
    p_ref = state.reference.p_ref_mev_c
    energy = np.hypot(p_ref * (1 + delta), MASS_MEV) - MASS_MEV
    emittances = []
    for position, momentum in ((x, ux), (y, uy)):
        covariance = np.cov(position, momentum * p_ref / MASS_MEV, ddof=0)
        # The determinant of a singular covariance can round slightly below zero.
        emittances.append(float(np.sqrt(max(0.0, np.linalg.det(covariance))) * 1e6))
    return {
        "particles": len(x), "charge_pc": -state.bunch_charge_c * 1e12, "p_ref_mev_c": p_ref,
        "mean_kinetic_mev": float(energy.mean()), "rms_energy_kev": float(energy.std() * 1e3),
        "sigma_x_mm": float(x.std() * 1e3), "sigma_y_mm": float(y.std() * 1e3),
        "sigma_t_ps": float(tau.std() / C_M_S * 1e12),
        "emit_nx_mm_mrad": emittances[0], "emit_ny_mm_mrad": emittances[1],
    }


def load_impact_reference(benchmark, working_point):
    case = benchmark / "comparison/impact-elegant-offline"
    handoff_path = case / "handoff.json"
    beam_path = case / "impact-native-entrance.json"
    _require(file_digest(handoff_path) == working_point["handoff_sha256"],
             "IMPACT-T handoff differs from the P0 working point")
    _require(file_digest(beam_path) == working_point["beam_metadata_sha256"],
             "IMPACT-T beam metadata differs from the P0 working point")
    return read_runtime_state(handoff_path), load_beam_state(beam_path)


def run_preinjector(request, output_root, *, progress=None, directory=None):
    from .particle_model import PARTICLE_RUN_KIND, _artifacts

    if directory is None:
        directory = Path(output_root).resolve() / f"shadow_{uuid.uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": "digital_shadow_run_v1", "kind": PARTICLE_RUN_KIND, "run_id": directory.name,
                "scope": "preinjector", "impact_variant": request.impact_variant, "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat()}
    manifest_path = directory / "manifest.json"
    result_path = directory / "result.json"
    write_runtime_state(manifest_path, manifest)
    if progress is None:
        progress = lambda _message: None

    try:
        write_runtime_state(directory / "request.json", request.as_metadata())
        benchmark = Path(request.impact_benchmark_dir).resolve()
        library_dir = Path(request.impact_library_dir).resolve()
        progress("Preparing P0 inputs")
        _require(library_dir.is_dir(), f"IMPACT-T library directory does not exist: {library_dir}")
        working_point = read_runtime_state(WORKING_POINT)
        handoff, frozen = load_impact_reference(benchmark, working_point)
        variant = request.impact_variant
        impact = stage_impact(benchmark, directory, working_point, variant=variant)
        deck_sha256 = file_digest(impact / DECK)
        manifest.update(working_point=working_point["id"], engine={
            "name": "IMPACT-T", "version": working_point["impact_version"],
            "executable": "ImpactTexe", "sha256": working_point["engine_sha256"],
        }, inputs_manifest_sha256=working_point["inputs_manifest_sha256"], deck_sha256=deck_sha256)
        manifest["input_hash"] = digest({
            "working_point_sha256": file_digest(WORKING_POINT),
            "request": request.as_metadata(), "implementation": file_digest(__file__), "deck_sha256": deck_sha256,
        })
        write_runtime_state(manifest_path, manifest)
        progress("Tracking P0 working point" if variant == IMPACT_NOMINAL else f"Tracking {IMPACT_VARIANT_LABELS[variant]}")
        run_impact(directory, library_dir, timeout_s=request.model_timeout_s, progress=progress)
        ensure_impact_finished(impact)
        progress("Exporting native-plane beam")
        detail = frozen.provenance.detail
        if variant == IMPACT_L01B_PROBE:
            detail += f" {IMPACT_VARIANT_LABELS[variant]}."
        beam, handoff = beam_at_boundary(impact, handoff, frozen, detail,
                                        stage="Digital Shadow pre-injector tracking")
        if variant == IMPACT_L01B_PROBE:
            handoff["upstream"] = {"working_point": working_point["id"], "variant": variant, "deck_sha256": deck_sha256}
        beam_path = save_beam_state(beam, directory / "entrance.json")
        handoff_path = directory / "handoff.json"
        write_runtime_state(handoff_path, handoff)
        result = {
            "kind": PARTICLE_RUN_KIND, "run_id": directory.name, "scope": "preinjector", "status": "complete",
            "input_hash": manifest["input_hash"], "impact_variant": variant, "deck_sha256": deck_sha256,
            "predictions": [], "comparisons": [],
            "beam_path": beam_path.name, "handoff_path": handoff_path.name,
            "beam_statistics": {"forward_particles": handoff["forward_particles"], **beam_stats(beam)},
        }
        artifacts = _artifacts(directory)
        write_runtime_state(result_path, result)
        artifacts[result_path.name] = file_digest(result_path)
        manifest.update(status="complete", impact_handoff=handoff, artifacts=artifacts,
                        boundary=read_runtime_state(beam_path)["boundary"])
    except KeyboardInterrupt:
        result_path.unlink(missing_ok=True)
        manifest.update(status="interrupted", error={"message": "IMPACT-T tracking interrupted"})
        return {"run_dir": str(directory), "status": "interrupted"}
    except (RuntimeError, ValueError, TypeError, KeyError, IndexError, OSError) as error:
        result_path.unlink(missing_ok=True)
        manifest.update(status="failed", error={"type": type(error).__name__, "message": str(error)})
        raise RuntimeError(f"Pre-injector model failed: {error}; see {manifest_path}") from error
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_runtime_state(manifest_path, manifest)
    return {**result, "run_dir": str(directory)}
