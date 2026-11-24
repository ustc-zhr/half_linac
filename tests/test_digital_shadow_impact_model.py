"""Exercise the offline IMPACT-T runner with a small diagnostic-engine fixture."""

from dataclasses import replace
import sys
from pathlib import Path

import numpy as np
import pytest

from half_linac.src.apps.digital_shadow.jobs import read_archive
from half_linac.src.shared.digital_shadow import impact_model
from half_linac.src.shared.digital_shadow.beam_state import (
    BeamBoundary, BeamParticles, BeamProvenance, BeamReference, BeamState6D, RFReference,
    load_beam_state, save_beam_state,
)
from half_linac.src.shared.digital_shadow.particle_model import ParticleModelRequest, run_particle_model
from half_linac.src.shared.digital_shadow.session import file_digest
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state


@pytest.fixture
def benchmark(tmp_path, monkeypatch):
    root = tmp_path / "final-benchmark"
    impact = root / "impact-t"
    inputs = impact / "input"
    inputs.mkdir(parents=True)
    deck = inputs / "ImpactT.in"
    deck.write_text("1 1\n1e-12 42000 1\n6 40000\n\n\n\n\n\n1.4919 60\n"
                    "0.1 0 0 112 5.9 2.25 2856000000 0 28\n"
                    "0.1 0 0 112 6.2 2.25 2856000000 90 28\n"
                    "0.1 0 0 112 9.1994809 2.25 2856000000 0 28\n")
    (inputs / "aperture-control.in").write_text("3 1\n9.2994809 0.03\n")
    write_runtime_state(inputs / "screen-map.json", {"0": 1})
    manifest = impact / "inputs.sha256"
    manifest.write_text("".join(f"{file_digest(path)}  input/{path.name}\n" for path in sorted(inputs.iterdir())
                                if not path.name.startswith(".")))
    engine = impact / "engine/ImpactTexe"
    engine.parent.mkdir()
    engine.write_text(f"#!{sys.executable}\n" + '''
import os
from pathlib import Path
import time

behavior = os.environ.get("SHADOW_IMPACT_FIXTURE")
if behavior == "timeout":
    time.sleep(30)
if behavior == "failure":
    raise SystemExit(4)
if behavior != "missing":
    Path("screens.tsv").write_text("""element id t x px y py pz passes
1 21 1.000e-9 0.003 0.02 0 0.01 220 1
1 21 0.999e-9 0.001 0.02 0 0.01 220 1
1 42 1.001e-9 0.002 0.01 0 0.02 220 1
1 9 1.1e-9 0.001 0 0 0 220 1
1 12 1e-9 0 0 0 0 -220 1
1 13 1e-9 0 0 0 0 220 0
""")
print(" before  destruct4:", flush=True)
''')
    engine.chmod(0o755)
    case = root / "comparison/impact-elegant-offline"
    reference = BeamState6D(
        BeamBoundary("ELEGANT_ENTRANCE", "half", "0" * 64, "ALL_MAIN", "C", 1, "entrance"),
        BeamReference(114.0, 1e-9, "fixture clock", "fixture bunch", "test",
                      (RFReference("S band", 2856e6, None),)),
        BeamProvenance("engine", "Fixture IMPACT-T", model_version="fixture"),
        BeamParticles([[0, 0, 0, 0, 0, 0]], [1], [1]),
        bunch_charge_c=-1e-12,
    )
    beam = save_beam_state(reference, case / "impact-native-entrance.json")
    handoff = case / "handoff.json"
    write_runtime_state(handoff, {
        "physical_screen_id": 1, "native_z_m": 9.2994809, "rf_end_z_m": 9.1994809,
        "drift_in_impact_m": 0.1, "extra_drift_m": 0,
        "frequency_Hz": 2856e6, "gate_clock_s": 1e-9, "gate_phase_deg": [-50, 10],
    })
    working_point = tmp_path / "working-point.json"
    write_runtime_state(working_point, {
        "id": "fixture", "impact_version": "fixture", "engine_sha256": file_digest(engine),
        "inputs_manifest_sha256": file_digest(manifest), "handoff_sha256": file_digest(handoff),
        "beam_metadata_sha256": file_digest(beam),
        "l01b_probe": {"start_m": "6.1565863", "end_m": "9.1994809", "frequency_hz": 2856000000,
                       "field_map_ids": [28], "cards": 1, "amplitude_scale": "1.01"},
    })
    monkeypatch.setattr(impact_model, "WORKING_POINT", working_point)
    library = tmp_path / "lib"
    library.mkdir()
    return ParticleModelRequest(scope="preinjector", impact_benchmark_dir=str(root), impact_library_dir=str(library),
                                model_timeout_s=0.5)


def test_single_impact_exports_native_beam_and_reopens_archive(benchmark, tmp_path):
    messages = []
    result = run_particle_model(None, benchmark, tmp_path / "runs", progress=messages.append)
    run = Path(result["run_dir"])
    archive = read_archive(run, verify_particle_inputs=True)
    assert (archive["backend"], archive["status"]) == ("offline", "complete")
    assert messages == ["Preparing P0 inputs", "Tracking P0 working point", "Exporting native-plane beam"]
    assert result["beam_statistics"]["forward_particles"] == 3
    assert result["beam_statistics"]["particles"] == 2
    assert all(np.isfinite(value) for value in result["beam_statistics"].values())
    beam = load_beam_state(run / result["beam_path"])
    np.testing.assert_array_equal(beam.payload.particle_ids, [21, 42])
    np.testing.assert_array_equal(beam.payload.coordinates[:, 0], [0.001, 0.002])
    assert archive["manifest"]["impact_handoff"]["source_sha256"] == file_digest(run / "impact/screens.tsv")
    assert not (run / "elegant").exists() and not (run / "machine_snapshot.json").exists()
    assert not (Path(benchmark.impact_benchmark_dir) / "impact-t/input/screens.tsv").exists()


def test_probe_patches_only_staged_deck_amplitude_and_archives_it(benchmark, tmp_path):
    source = Path(benchmark.impact_benchmark_dir) / "impact-t/input/ImpactT.in"
    original = source.read_text()
    request = replace(benchmark, impact_variant=impact_model.IMPACT_L01B_PROBE)
    result = run_particle_model(None, request, tmp_path / "runs")
    run = Path(result["run_dir"])
    deck = run / "impact/ImpactT.in"
    assert source.read_text() == original
    assert deck.read_text() == original.replace("6.2 2.25", "6.2 2.2725")
    archive = read_archive(run, verify_particle_inputs=True)
    assert archive["request"]["impact_variant"] == request.impact_variant
    assert archive["manifest"]["deck_sha256"] == file_digest(deck)
    assert archive["manifest"]["impact_handoff"]["upstream"]["deck_sha256"] == file_digest(deck)
    deck.write_text(original)
    with pytest.raises(ValueError, match="Archive integrity check failed: ImpactT.in"):
        read_archive(run, verify_particle_inputs=True)


@pytest.mark.parametrize(("behavior", "error"), [
    ("failure", "exited with 4"), ("missing", "Missing IMPACT-T native-plane records"),
    ("timeout", "exceeded its tracking timeout"), ("checksum", "input checksum differs"),
])
def test_single_impact_preserves_failed_run(benchmark, tmp_path, monkeypatch, behavior, error):
    monkeypatch.setenv("SHADOW_IMPACT_FIXTURE", behavior)
    if behavior == "checksum":
        (Path(benchmark.impact_benchmark_dir) / "impact-t/input/ImpactT.in").write_text("modified")
    with pytest.raises(RuntimeError, match=error):
        run_particle_model(None, benchmark, tmp_path / "runs")
    manifest, = (tmp_path / "runs").glob("shadow_*/manifest.json")
    assert read_runtime_state(manifest)["status"] == "failed"
    assert not (manifest.parent / "result.json").exists()
    assert (manifest.parent / "impact/run.log").is_file() == (behavior != "checksum")


def test_single_impact_interruption_reaps_engine_and_keeps_log(benchmark, tmp_path, monkeypatch):
    def interrupt(process, *_args):
        assert process.poll() is None
        raise KeyboardInterrupt

    monkeypatch.setenv("SHADOW_IMPACT_FIXTURE", "timeout")
    monkeypatch.setattr(impact_model, "_wait_for_impact", interrupt)
    result = run_particle_model(None, benchmark, tmp_path / "runs")
    run = Path(result["run_dir"])
    assert result["status"] == read_archive(run)["status"] == "interrupted"
    assert not (run / "result.json").exists()
    assert (run / "impact/run.log").is_file()
    assert int((run / "impact/exit-status.txt").read_text()) < 0
