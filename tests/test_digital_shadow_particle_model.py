"""Offline particle input, model settings and archive regression checks."""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from half_linac.src.apps.digital_shadow.jobs import read_archive
from half_linac.src.shared.digital_shadow import coupled_model, particle_model
from half_linac.src.shared.digital_shadow.beam_state import (
    BeamBoundary, BeamParticles, BeamProvenance, BeamReference, BeamState6D, RFReference, save_beam_state,
)
from half_linac.src.shared.digital_shadow.example import write_example
from half_linac.src.shared.digital_shadow.impact_model import IMPACT_L01B_PROBE, IMPACT_NOMINAL
from half_linac.src.shared.digital_shadow.particle_model import ParticleModelRequest, run_particle_model
from half_linac.src.shared.digital_shadow.session import file_digest
from half_linac.src.shared.elegant_backend.beam_sdds import read_elegant_particles
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.machine_state import load_snapshot, save_snapshot
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state


@pytest.fixture
def model(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The offline model may not access PVs")

    for name in ("PV", "caget", "caput", "caget_many", "caput_many"):
        monkeypatch.setattr(f"epics.{name}", forbidden)
    monkeypatch.delenv("RPN_DEFNS", raising=False)
    lattice = tmp_path / "source.lte"
    lattice.write_text(
        'C: CHARGE, TOTAL=5.5e-10\n'
        'QT02: QUAD, L=0.1, K1=1\n'
        'PRF06: WATCH, MODE="coordinates", FILENAME="unused.sdds"\n'
        'PRF07: WATCH, MODE="coordinates", FILENAME="unused.sdds"\n'
        'ALL_MAIN: LINE=(C,QT02,PRF06,PRF07)\n'
    )
    context = load_model_context("half")
    backend = context.model_backend
    return replace(context, model_backend=replace(backend, config={**backend.config, "source_lattice": lattice}))


@pytest.fixture
def engine(tmp_path, monkeypatch):
    calls = []
    executable = tmp_path / "elegant"
    executable.write_text("elegant engine fixture")
    monkeypatch.setenv("PATH", str(tmp_path))
    executable.chmod(0o755)

    def run(ele, log, *, workdir, timeout_s, env):
        calls.append((ele, timeout_s))
        (workdir / log).write_text("fixture tracking log")
        (workdir / "screen-0.sdds").write_text("fixture output")

    monkeypatch.setattr(particle_model, "run_elegant_input", run)
    monkeypatch.setattr(particle_model, "_load_watch_rms_from_sdds", lambda path: (0.4, 0.5))
    return calls


@pytest.fixture
def entrance(model, tmp_path):
    state = BeamState6D(
        BeamBoundary("ELEGANT_ENTRANCE", "half", file_digest(model.model_backend.config["source_lattice"]),
                     "ALL_MAIN", "C", 1, "entrance"),
        BeamReference(114.16, 0, "fixture entrance clock", "fixture bunch", "synthetic"),
        BeamProvenance("synthetic", "Two particles at the model entrance"),
        BeamParticles([[1e-4, 0, 2e-4, 0, 1e-3, 0], [3e-4, 0, 1e-4, 0, -2e-3, 0.01]],
                      [1, 1], np.array([5, 7], dtype=np.uint64)),
        bunch_charge_c=-3e-10,
    )
    return state, save_beam_state(state, tmp_path / "beam.json")


def test_generated_model_archives_partial_predictions(model, engine, tmp_path):
    result = run_particle_model(model, ParticleModelRequest(model_timeout_s=8), tmp_path / "runs")
    directory = Path(result["run_dir"])
    state = read_runtime_state(directory / "inputs/evaluated_state.json")
    assert "bunched_beam" in state["control"] and "sdds_beam" not in state["control"]
    assert state["control"]["run_setup"]["random_number_seed"] == "9876543210"
    assert engine == [("tracking.ele", 8)]
    archive = read_archive(directory)
    assert (archive["status"], archive["backend"]) == ("partial", "offline")
    rows = archive["result"]["predictions"]
    assert [(row["key"], row["predicted"], row["unit"], row["status"]) for row in rows] == [
        ("PRF06/sigx", 0.4, "mm", "predicted"), ("PRF06/sigy", 0.5, "mm", "predicted"),
        ("PRF07/sigx", None, "mm", "unavailable"), ("PRF07/sigy", None, "mm", "unavailable"),
    ]
    assert archive["result"]["comparisons"] == []
    assert archive["manifest"]["artifacts"]["inputs/source.lte"] == file_digest(model.model_backend.config["source_lattice"])


def test_native_sdds_is_frozen_and_reusable(model, engine, tmp_path):
    source = tmp_path / "source bunch.dat"
    source.write_text("native particle fixture")
    request = ParticleModelRequest(beam_source="sdds", beam_path=str(source))
    directory = Path(run_particle_model(model, request, tmp_path / "runs")["run_dir"])
    source.unlink()
    archive = read_archive(directory, verify_particle_inputs=True)
    assert archive["manifest"]["beam_file"] == "source bunch.dat"
    restored = ParticleModelRequest.from_dict(archive["request"], base=directory)
    assert Path(restored.beam_path).read_text() == "native particle fixture"
    control = read_runtime_state(directory / "inputs/evaluated_state.json")["control"]
    source_settings = control["sdds_beam"]
    assert source_settings["input"] == '"../inputs/input.sdds"'
    assert source_settings["center_arrival_time"] == source_settings["center_transversely"] == source_settings["reverse_t_sign"] == "0"
    assert source_settings["sample_interval"] == source_settings["sample_fraction"] == "1"
    assert "bunched_beam" not in control
    Path(restored.beam_path).write_text("modified")
    with pytest.raises(ValueError, match="Archive integrity check failed"):
        read_archive(directory, verify_particle_inputs=True)


def test_archive_configures_charge_reference_and_preserves_particles(model, engine, entrance, tmp_path):
    beam, source = entrance
    directory = Path(run_particle_model(model, ParticleModelRequest(beam_source="archived", beam_path=str(source)),
                                        tmp_path / "runs")["run_dir"])
    state = read_runtime_state(directory / "inputs/evaluated_state.json")
    setup = state["control"]["run_setup"]
    assert "p_central" not in setup
    assert float(setup["p_central_mev"]) == beam.reference.p_ref_mev_c
    assert setup["always_change_p0"] == "0"
    assert float(state["lattice"]["C"]["TOTAL"]) == abs(beam.bunch_charge_c)
    converted = read_elegant_particles(directory / "inputs/input.sdds", beam.reference)
    np.testing.assert_allclose(converted.coordinates, beam.payload.coordinates, atol=1e-14, rtol=1e-12)
    np.testing.assert_array_equal(converted.particle_ids, beam.payload.particle_ids)
    assert read_archive(directory, verify_particle_inputs=True)["request"]["beam_path"] == "inputs/entrance.json"


@pytest.mark.parametrize(("condition", "error"), [
    ("plane", "does not match the ELEGANT model entrance"),
    ("moments", "moments alone cannot be tracked"),
    ("charge", "requires an explicit bunch charge"),
    ("weights", "equal"),
])
def test_archive_rejects_incompatible_entrance(model, engine, entrance, tmp_path, condition, error):
    beam, source = entrance
    changes = {
        "plane": replace(beam, boundary=replace(beam.boundary, edge="exit")),
        "moments": beam.as_moments(),
        "charge": replace(beam, bunch_charge_c=None),
        "weights": replace(beam, payload=replace(beam.payload, weights=[1, 2])),
    }
    save_beam_state(changes[condition], source)
    with pytest.raises(RuntimeError, match=error):
        run_particle_model(model, ParticleModelRequest(beam_source="archived", beam_path=str(source)), tmp_path / "runs")
    assert not engine
    manifest, = (tmp_path / "runs").glob("shadow_*/manifest.json")
    assert read_runtime_state(manifest)["status"] == "failed"
    assert not (manifest.parent / "result.json").exists()


def test_snapshot_uses_required_saved_k1(model, engine, tmp_path):
    source = write_example(tmp_path / "snapshot", vm=True) / "machine_snapshot.json"
    snapshot = load_snapshot(source)
    entries = tuple(replace(entry, value=2.5) if entry.key == "QT02/K1" else entry for entry in snapshot.entries)
    save_snapshot(source, replace(snapshot, entries=entries))
    request = ParticleModelRequest(parameter_source="snapshot", snapshot_path=str(source))
    directory = Path(run_particle_model(model, request, tmp_path / "runs")["run_dir"])
    state = read_runtime_state(directory / "inputs/evaluated_state.json")
    assert float(state["lattice"]["QT02"]["K1"]) == 2.5
    assert float(state["lattice"]["C"]["TOTAL"]) == 5.5e-10
    assert read_archive(directory)["request"]["snapshot_path"] == "machine_snapshot.json"
    save_snapshot(source, replace(snapshot, entries=tuple(entry for entry in entries if entry.key != "QT02/K1")))
    with pytest.raises(RuntimeError, match="missing QT02/K1"):
        run_particle_model(model, request, tmp_path / "runs")
    assert len(engine) == 1


def test_impact_handoff_archives_source_hash_and_requires_zero_extra_drift(model, engine, entrance, tmp_path):
    beam, source = entrance
    frequency = 2.856e9
    beam = replace(beam, reference=replace(beam.reference, rf=(RFReference("S band", frequency, None),)))
    save_beam_state(beam, source)
    snapshot = write_example(tmp_path / "snapshot", vm=True) / "machine_snapshot.json"
    handoff = tmp_path / "handoff.json"
    metadata = {
        "source": "screens.tsv", "native_z_m": 9.2994809, "rf_end_z_m": 9.1994809,
        "drift_in_impact_m": 0.1, "extra_drift_m": 0, "gate_phase_deg": [-50, 10],
        "gate_clock_s": beam.reference.t_ref_s, "frequency_Hz": frequency,
        "selected_particles": 2, "p_ref_MeV_c": beam.reference.p_ref_mev_c, "selected_charge_pC": 300,
    }
    write_runtime_state(handoff, metadata)
    request = ParticleModelRequest(beam_source="archived", beam_path=str(source), parameter_source="snapshot",
                                   snapshot_path=str(snapshot), handoff_path=str(handoff))
    result = run_particle_model(model, request, tmp_path / "runs")
    directory = Path(result["run_dir"])
    archive = read_archive(directory, verify_particle_inputs=True)
    assert archive["manifest"]["impact_handoff"] == metadata
    assert archive["manifest"]["particle_input"]["source_npz_sha256"] == read_runtime_state(source)["data"]["sha256"]
    assert archive["request"]["handoff_path"] == "inputs/impact-handoff.json"

    write_runtime_state(handoff, {**metadata, "extra_drift_m": 0.1})
    with pytest.raises(RuntimeError, match="zero extra drift"):
        run_particle_model(model, request, tmp_path / "runs")
    assert len(engine) == 1

    write_runtime_state(directory / archive["request"]["handoff_path"], {})
    with pytest.raises(ValueError, match="Archive integrity check failed: impact-handoff.json"):
        read_archive(directory, verify_particle_inputs=True)


def test_coupled_request_requires_snapshot_and_has_separate_timeouts(tmp_path):
    paths = {"impact_benchmark_dir": str(tmp_path / "benchmark"), "impact_library_dir": str(tmp_path / "lib")}
    with pytest.raises(ValueError, match="saved VM snapshot"):
        ParticleModelRequest(scope="coupled", **paths)
    request = ParticleModelRequest(scope="coupled", parameter_source="snapshot",
                                   snapshot_path=str(tmp_path / "vm.json"), **paths)
    assert (request.model_timeout_s, request.elegant_timeout_s) == (7200, 300)
    assert ParticleModelRequest.from_dict(request.as_metadata(), base=tmp_path) == request
    legacy = request.as_metadata()
    del legacy["impact_variant"]
    assert ParticleModelRequest.from_dict(legacy, base=tmp_path).impact_variant == IMPACT_NOMINAL
    with pytest.raises(ValueError, match="supported IMPACT-T working point"):
        replace(request, impact_variant="arbitrary-value")
    with pytest.raises(ValueError, match="settings require an IMPACT-T scope"):
        ParticleModelRequest(impact_variant=IMPACT_L01B_PROBE)
    with pytest.raises(ValueError, match="uses the P0 benchmark inputs"):
        replace(request, beam_path="external.json")


@pytest.mark.parametrize("vm", [True, False])
def test_coupled_rejects_incomplete_or_real_snapshot_before_tracking(model, engine, entrance, monkeypatch, tmp_path, vm):
    beam, _ = entrance
    source = write_example(tmp_path / "snapshot", vm=vm) / "machine_snapshot.json"
    snapshot = load_snapshot(source)
    if vm:
        save_snapshot(source, replace(snapshot, entries=tuple(entry for entry in snapshot.entries if entry.key != "QT02/K1")))
    monkeypatch.setattr(coupled_model, "load_impact_reference", lambda *args: ({}, beam))

    def forbidden(*args, **kwargs):
        pytest.fail("Coupled tracking must validate the snapshot before starting IMPACT-T")

    monkeypatch.setattr(coupled_model, "run_preinjector", forbidden)
    request = ParticleModelRequest(scope="coupled", parameter_source="snapshot", snapshot_path=str(source),
                                   impact_benchmark_dir=str(tmp_path / "benchmark"), impact_library_dir=str(tmp_path / "lib"))
    with pytest.raises(RuntimeError, match="missing QT02/K1" if vm else "saved HALF VM snapshot"):
        run_particle_model(model, request, tmp_path / "runs")
    manifest, = (tmp_path / "runs").glob("shadow_*/manifest.json")
    assert read_runtime_state(manifest)["stages"]["upstream"]["status"] == "pending"


@pytest.mark.parametrize("downstream_outcome", ["complete", "failed", "interrupted"])
def test_coupled_uses_current_upstream_and_prefrozen_snapshot(model, engine, entrance, monkeypatch, tmp_path,
                                                            downstream_outcome):
    beam, _ = entrance
    source = write_example(tmp_path / "snapshot", vm=True) / "machine_snapshot.json"
    snapshot = load_snapshot(source)
    monkeypatch.setattr(coupled_model, "load_impact_reference", lambda *args: ({}, beam))
    reported = []

    def upstream(request, output_root, *, directory, progress):
        assert request.scope == "preinjector"
        assert request.model_timeout_s == 30
        assert request.impact_variant == IMPACT_L01B_PROBE
        assert load_snapshot(output_root / "machine_snapshot.json") == snapshot
        source.unlink()
        directory.mkdir()
        save_beam_state(beam, directory / "current-run.json")
        write_runtime_state(directory / "handoff.json", {"native_z_m": 9.2994809, "extra_drift_m": 0})
        return {"status": "complete", "input_hash": "upstream", "beam_path": "current-run.json",
                "handoff_path": "handoff.json", "beam_statistics": {"selected_particles": 2}, "deck_sha256": "probe-sha"}

    def downstream(context, request, directory):
        assert Path(request.beam_path) == directory.parent / "upstream/current-run.json"
        assert Path(request.handoff_path) == directory.parent / "upstream/handoff.json"
        assert Path(request.snapshot_path) == directory.parent / "machine_snapshot.json"
        assert load_snapshot(request.snapshot_path) == snapshot
        assert request.model_timeout_s == 6
        if downstream_outcome == "failed":
            raise RuntimeError("downstream fixture error")
        if downstream_outcome == "interrupted":
            raise KeyboardInterrupt
        return {"status": "complete", "input_hash": "downstream", "predictions": []}

    monkeypatch.setattr(coupled_model, "run_preinjector", upstream)
    monkeypatch.setattr(coupled_model, "run_downstream", downstream)
    monkeypatch.setattr(coupled_model, "_end_particles", lambda path: 1)
    request = ParticleModelRequest(scope="coupled", parameter_source="snapshot", snapshot_path=str(source),
                                   impact_benchmark_dir=str(tmp_path / "benchmark"),
                                   impact_library_dir=str(tmp_path / "lib"), model_timeout_s=30, elegant_timeout_s=6,
                                   impact_variant=IMPACT_L01B_PROBE)
    runs = tmp_path / "runs"
    if downstream_outcome == "failed":
        with pytest.raises(RuntimeError, match="downstream fixture error"):
            run_particle_model(model, request, runs, progress=reported.append)
    else:
        result = run_particle_model(model, request, runs, progress=reported.append)
        assert result["status"] == downstream_outcome
        if downstream_outcome == "complete":
            assert result["end_particles"] == 1
    manifest_path, = runs.glob("shadow_*/manifest.json")
    manifest = read_runtime_state(manifest_path)
    assert manifest["status"] == manifest["stages"]["downstream"]["status"] == downstream_outcome
    assert manifest["stages"]["upstream"]["status"] == "complete"
    assert manifest["snapshot_id"] == snapshot.snapshot_id
    assert (manifest["impact_variant"], manifest["deck_sha256"]) == (IMPACT_L01B_PROBE, "probe-sha")
    assert (manifest_path.parent / "upstream/current-run.json").is_file()
    assert (manifest_path.parent / "result.json").exists() == (downstream_outcome == "complete")
    assert reported[-1].startswith("ELEGANT · Tracking")
