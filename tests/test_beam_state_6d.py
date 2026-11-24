from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import shutil

import numpy as np
import pytest

from half_linac.src.shared.digital_shadow.beam_state import (
    BeamBoundary,
    BeamLoss,
    BeamMoments,
    BeamParticles,
    BeamProvenance,
    BeamReference,
    BeamState6D,
    C_M_S,
    RFReference,
    load_beam_state,
    resolve_boundary,
    save_beam_state,
)
from half_linac.src.shared.beam_matrix import ELECTRON_REST_ENERGY_MEV
from half_linac.src.shared.elegant_backend.beam_sdds import read_elegant_particles, write_elegant_beam
from half_linac.src.shared.elegant_backend.parser import ElegantParser, _new_legacy_sdds_dataset
from half_linac.src.shared.elegant_runtime import run_elegant_input


@pytest.fixture
def state():
    return BeamState6D(
        boundary=BeamBoundary("TEST_EXIT", "synthetic", "a" * 64, "TEST", "D", 2, "exit"),
        reference=BeamReference(2.5, 3e-9, "synthetic trigger", "bunch 1", "design particle"),
        provenance=BeamProvenance("synthetic", "Three-particle fixture"),
        payload=BeamParticles(
            [[1e-3, 0.015, 2e-3, -0.008, -1e-3, -0.02],
             [-2e-3, -0.010, -1e-3, 0.003, 2e-3, 0.01],
             [4e-3, 0.003, 3e-3, 0.002, 3e-3, 0.05]],
            [1, 2, 3],
            np.array([2**63 + 1, 2**53 + 1, 11], dtype=np.uint64),
        ),
        bunch_charge_c=-3e-12,
    )


def test_boundary_occurrence_and_edge_require_the_selected_lattice(state):
    boundary = state.boundary
    source = dict(machine_id="synthetic", lattice_sha256="a" * 64, line="TEST")
    line = ["D", "Q", "D", "SCREEN"]
    assert resolve_boundary("TEST_EXIT", {"TEST_EXIT": boundary}, line, **source) == 3
    entrance = replace(boundary, boundary_id="TEST_ENTRANCE", edge="entrance")
    assert resolve_boundary("TEST_ENTRANCE", {"TEST_ENTRANCE": entrance}, line, **source) == 2
    with pytest.raises(ValueError, match="unresolved_boundary: L01B_EXIT"):
        resolve_boundary("L01B_EXIT", {}, line, **source)
    with pytest.raises(ValueError, match="differs"):
        resolve_boundary("TEST_EXIT", {"TEST_EXIT": boundary}, line, **{**source, "lattice_sha256": "b" * 64})
    with pytest.raises(ValueError, match="no model occurrence"):
        resolve_boundary("TEST_EXIT", {"TEST_EXIT": boundary}, line[:2], **source)


def test_exchange_round_trips_particles_and_complete_weighted_moments(state, tmp_path):
    rf = RFReference("test-rf", 2856e6, -math.pi / 2, "synthetic cal")
    state = replace(state, reference=replace(state.reference, rf=(rf,)))
    replay = load_beam_state(save_beam_state(state, tmp_path / "particles.json"))
    assert replay.boundary == state.boundary
    assert replay.reference == state.reference
    assert replay.provenance == state.provenance
    assert replay.bunch_charge_c == state.bunch_charge_c
    for field in ("coordinates", "weights", "particle_ids"):
        np.testing.assert_array_equal(getattr(replay.payload, field), getattr(state.payload, field))
    assert not replay.payload.coordinates.flags.writeable

    moment_state = load_beam_state(save_beam_state(replay.as_moments(), tmp_path / "moments.json"))
    expected = np.repeat(state.payload.coordinates, [1, 2, 3], axis=0)
    np.testing.assert_allclose(moment_state.payload.mean, expected.mean(axis=0), atol=1e-17)
    np.testing.assert_allclose(moment_state.payload.covariance, np.cov(expected.T, bias=True), atol=1e-18)
    assert moment_state.payload.covariance[0, 4] != 0
    assert moment_state.payload.covariance[1, 5] != 0
    assert moment_state.payload.covariance.shape == (6, 6)


def test_exchange_rejects_tampering_and_unit_mismatch(state, tmp_path):
    path = save_beam_state(state, tmp_path / "beam.json")
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "units": ["mm", "1", "mm", "1", "s", "1"]}))
    with pytest.raises(ValueError, match="units"):
        load_beam_state(path)
    path.write_text(json.dumps(record))
    artifact = tmp_path / record["data"]["path"]
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_beam_state(path)


@pytest.mark.parametrize("coordinates", [[[0] * 5], [[0, 0, 0, 0, 0, -1]], [[0, 1, 0, 1, 0, 0]]])
def test_rejects_invalid_coordinates(coordinates):
    with pytest.raises(ValueError, match="Nx6|forward"):
        BeamParticles(coordinates, [1])


def test_rejects_incompatible_weights_ids_and_moments(state):
    with pytest.raises(ValueError, match="positive weight"):
        replace(state.payload, weights=[1, 0, 1])
    with pytest.raises(ValueError, match="unique positive integers"):
        replace(state.payload, particle_ids=[1, 1, 4])
    covariance = np.eye(6)
    covariance[0, 5] = covariance[5, 0] = 2
    with pytest.raises(ValueError, match="semidefinite"):
        BeamMoments(np.zeros(6), covariance)
    with pytest.raises(ValueError, match="symmetric"):
        BeamMoments(np.zeros(6), np.tril(covariance))
    BeamMoments(np.zeros(6), np.zeros((6, 6)))


def test_reference_requires_valid_time_energy_phase_and_clock(state):
    for field, invalid in (("clock_id", ""), ("t_ref_s", float("nan")), ("p_ref_mev_c", 0.0)):
        with pytest.raises(ValueError, match=field):
            replace(state.reference, **{field: invalid})
    with pytest.raises(ValueError, match="phase_rad"):
        RFReference("rf", 2856e6, math.pi)
    with pytest.raises(ValueError, match="calibration_version"):
        RFReference("rf", 2856e6, 0)
    assert RFReference("rf", 2856e6, None).phase_rad is None


def test_loss_preserves_signed_charge_and_full_loss_has_no_payload(state):
    loss = BeamLoss("TEST_ENTRANCE", 4, 3, -4e-12, -3e-12, "synthetic aperture")
    assert loss.lost_charge_c == pytest.approx(1e-12, abs=1e-24)
    assert loss.loss_fraction == pytest.approx(0.25)
    assert replace(state, loss=loss).loss == loss
    assert replace(state, loss=loss).as_moments().loss == loss
    total = BeamLoss("TEST_ENTRANCE", 4, 0, -4e-12, 0, "synthetic aperture")
    assert total.loss_fraction == 1
    with pytest.raises(ValueError, match="survivors"):
        replace(state, loss=total)


def test_elegant_sdds_roundtrip_preserves_timing_and_uint64_ids(state, tmp_path):
    path = tmp_path / "beam.sdds"
    with pytest.raises(ValueError, match="requires equal weights"):
        write_elegant_beam(state, path)
    with pytest.raises(ValueError, match="requires particles"):
        write_elegant_beam(state.as_moments(), path)
    state = replace(state, payload=replace(state.payload, weights=[1, 1, 1]))
    write_elegant_beam(state, path, time_offset_s=-2e-9)
    result = read_elegant_particles(path, state.reference, time_offset_s=-2e-9)
    np.testing.assert_allclose(result.coordinates, state.payload.coordinates, rtol=1e-12, atol=1e-16)
    np.testing.assert_array_equal(result.particle_ids, state.payload.particle_ids)
    with pytest.raises(ValueError, match="requested Pass"):
        read_elegant_particles(path, state.reference, expected_pass=0)


@pytest.mark.skipif(os.environ.get("HALF_ELEGANT_BEAM_INTEGRATION") != "1", reason="requires standalone ELEGANT")
@pytest.mark.parametrize("length,aperture", [(0.0, False), (0.1, False), (0.1, True)])
def test_elegant_tracks_an_isolated_synthetic_segment(state, tmp_path, length, aperture):
    lattice = tmp_path / "test.lte"
    source = [
        "START: MARK",
        f"C: CHARGE,TOTAL={abs(state.bunch_charge_c)}",
        f"D: EDRIFT,L={length}",
        'W: WATCH,MODE="coordinates",FILENAME="watch.sdds"',
    ]
    elements = ["START", "C", "D", "W"]
    if aperture:
        source.append("AP: ECOL,L=0,X_MAX=0.0035,Y_MAX=0.01")
        elements.insert(-1, "AP")
    source.append(f"TEST: LINE=({','.join(elements)})")
    lattice.write_text("\n".join(source) + "\n")
    lattice_sha256 = hashlib.sha256(lattice.read_bytes()).hexdigest()
    boundary_in = BeamBoundary("TEST_START", "synthetic", lattice_sha256, "TEST", "START", 1, "entrance")
    boundary_out = replace(boundary_in, boundary_id="TEST_END", element="W", edge="exit")

    state = replace(state, boundary=boundary_in, payload=replace(
        state.payload, weights=[1, 1, 1], particle_ids=np.array([101, 102, 103], dtype=np.uint64),
    ))
    state = load_beam_state(save_beam_state(state, tmp_path / "input.json"))
    time_offset = -1e-9
    write_elegant_beam(state, tmp_path / "input.sdds", time_offset_s=time_offset)
    template = tmp_path / "test.ele"
    template.write_text(f"""
&run_setup
  lattice = "test.lte", use_beamline = "TEST",
  p_central_mev = {state.reference.p_ref_mev_c},
  default_order = 1, always_change_p0 = 0,
&end
&run_control n_steps = 1 &end
&sdds_beam
  input = "input.sdds", input_type = "elegant",
  center_arrival_time = 0, center_transversely = 0, reverse_t_sign = 0,
  sample_interval = 1, sample_fraction = 1, p_lower = 0, p_upper = 0,
&end
&track &end
""")
    frozen = ElegantParser(lattice, template, "TEST").build_runtime_state()
    bindings = {boundary.boundary_id: boundary for boundary in (boundary_in, boundary_out)}
    selector = dict(machine_id="synthetic", lattice_sha256=lattice_sha256, line="TEST")
    assert resolve_boundary("TEST_START", bindings, frozen["usedline"], **selector) == 0
    assert resolve_boundary("TEST_END", bindings, frozen["usedline"], **selector) == len(elements)
    run_elegant_input(template.name, "test.log", workdir=tmp_path, timeout_s=10)

    p_ref = state.reference.p_ref_mev_c
    total_energy_ref = math.hypot(p_ref, ELECTRON_REST_ENERGY_MEV)
    advance = length * total_energy_ref / p_ref
    reference_out = replace(state.reference, t_ref_s=state.reference.t_ref_s + advance / C_M_S)
    output = tmp_path / "watch.sdds"
    particles = read_elegant_particles(output, reference_out, time_offset_s=time_offset, expected_step=1, expected_pass=0)
    dataset = _new_legacy_sdds_dataset()
    dataset.load(str(output))
    charge = -float(dataset.parameterData[dataset.parameterName.index("Charge")][0])
    loss = BeamLoss(boundary_in.boundary_id, len(state.payload.coordinates), len(particles.coordinates),
                    state.bunch_charge_c, charge, "ELEGANT WATCH particle count and charge")
    engine_hash = hashlib.sha256(Path(shutil.which("elegant")).read_bytes()).hexdigest()
    provenance = BeamProvenance(
        "engine", "Standalone ELEGANT with synthetic input.json", model_version=f"sha256:{engine_hash}",
        stage="synthetic tracking",
    )
    result = BeamState6D(boundary_out, reference_out, provenance, particles, charge, loss)
    result = load_beam_state(save_beam_state(result, tmp_path / "output.json"))

    expected = state.payload.coordinates.copy()
    momentum = p_ref * (1 + expected[:, 5])
    p_s = np.sqrt(momentum**2 - (p_ref * expected[:, 1])**2 - (p_ref * expected[:, 3])**2)
    expected[:, 0] += length * p_ref * expected[:, 1] / p_s
    expected[:, 2] += length * p_ref * expected[:, 3] / p_s
    expected[:, 4] += length * np.hypot(momentum, ELECTRON_REST_ENERGY_MEV) / p_s - advance
    count = 2 if aperture else 3
    order = np.argsort(result.payload.particle_ids)
    np.testing.assert_array_equal(result.payload.particle_ids[order], state.payload.particle_ids[:count])
    np.testing.assert_allclose(result.payload.coordinates[order], expected[:count], rtol=1e-11, atol=2e-14)
    np.testing.assert_allclose(
        np.sqrt(result.as_moments().payload.covariance.diagonal()),
        np.std(expected[:count], axis=0), rtol=1e-11, atol=2e-14,
    )
    assert result.loss.lost_charge_c == pytest.approx((3 - count) * 1e-12, abs=1e-24)
