#!/usr/bin/env python3
"""Measure sensitivity to a signed, field-free offset at the HALF ELEGANT entrance."""

import argparse
from dataclasses import replace
from math import isfinite
from pathlib import Path
import sys
from uuid import uuid4

import numpy as np

from check_digital_shadow_p0 import SCREENS, _columns, _require, check_run
from half_linac.src.apps.digital_shadow.jobs import read_archive
from half_linac.src.shared.beam_matrix import ELECTRON_REST_ENERGY_MEV
from half_linac.src.shared.digital_shadow.beam_state import (
    C_M_S, load_beam_state_archive, save_beam_state,
)
from half_linac.src.shared.digital_shadow.particle_model import ParticleModelRequest, run_particle_model
from half_linac.src.shared.elegant_backend.beam_sdds import read_elegant_particles
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state


def drift_probe(beam, offset_m, source_sha256):
    """Transport selected particles to a nearby parallel plane in a field-free drift."""
    coordinates = beam.payload.coordinates.copy()
    x, ux, y, uy, tau, delta = coordinates.T
    p_rel = 1 + delta
    pz_rel = np.sqrt(p_rel * p_rel - ux * ux - uy * uy)
    x += offset_m * ux / pz_rel
    y += offset_m * uy / pz_rel
    tau += offset_m * np.hypot(p_rel, ELECTRON_REST_ENERGY_MEV / beam.reference.p_ref_mev_c) / pz_rel
    detail = (
        f"HALF boundary sensitivity with signed field-free offset {offset_m:+.9g} m from the P0 native plane. "
        f"Original gate membership and absolute clock retained. Source particle NPZ SHA-256 {source_sha256}."
    )
    return replace(beam, payload=replace(beam.payload, coordinates=coordinates),
                   provenance=replace(beam.provenance, detail=detail, stage="offline boundary sensitivity"))


def compare_offset(p0, output_root, offset_mm):
    _require(isfinite(offset_mm) and offset_mm != 0, "Enter a finite nonzero offset in mm")
    reference = p0 / "vm-same-working-point"
    snapshot = reference / "vm-machine-snapshot.json"
    beam_path = p0 / "impact-native-entrance.json"
    beam, beam_record = load_beam_state_archive(beam_path)
    source_sha256 = beam_record["data"]["sha256"]
    directory = output_root / f"boundary_{uuid4().hex}"

    context = load_model_context("half")
    request = ParticleModelRequest(beam_source="archived", beam_path=str(beam_path), parameter_source="snapshot",
                                   snapshot_path=str(snapshot), handoff_path=str(p0 / "handoff.json"),
                                   model_timeout_s=300)
    baseline_result = run_particle_model(context, request, directory / "baseline")
    baseline = check_run(baseline_result["run_dir"], reference)
    _require(baseline["status"] == "passed", "Zero-offset P0 regression failed")

    shifted = drift_probe(beam, offset_mm * 1e-3, source_sha256)
    probe_path = save_beam_state(shifted, directory / "probe-entrance.json")
    probe_request = replace(request, beam_path=str(probe_path), handoff_path=None)
    probe_result = run_particle_model(context, probe_request, directory / "probe")
    probe_dir = Path(probe_result["run_dir"])
    archive = read_archive(probe_dir, verify_particle_inputs=True)
    _require(archive["status"] == "complete", "Offset tracking is incomplete")
    _require(read_runtime_state(probe_dir / "model_snapshot.json")
             == read_runtime_state(Path(baseline["run_dir"]) / "model_snapshot.json"),
             "Downstream VM settings differ between runs")

    recovered = read_elegant_particles(probe_dir / "inputs/input.sdds", shifted.reference)
    np.testing.assert_array_equal(recovered.particle_ids, beam.payload.particle_ids)
    np.testing.assert_allclose(recovered.coordinates, shifted.payload.coordinates, rtol=1e-12, atol=1e-14)
    centroid = _columns(probe_dir / "elegant/tracking.cen")
    entrance = int(centroid["Particles"][0])
    _require(entrance == baseline["entrance_particles"] and float(centroid["Charge"][1]) == -beam.bunch_charge_c,
             "Offset entrance count or charge differs")
    _require(centroid["pCentral"][0] == _columns(Path(baseline["run_dir"]) / "elegant/tracking.cen")["pCentral"][0],
             "Offset entrance reference momentum differs")
    rms0, rms1 = ({row["key"]: row["predicted"] for row in result["predictions"]}
                  for result in (baseline_result, probe_result))
    keys = [f"{name}/sig{axis}" for name in SCREENS for axis in "xy"]
    _require(rms0.keys() == rms1.keys() == set(keys), "Both runs need 26 screen RMS predictions")
    screens = [{"key": key, "baseline_mm": rms0[key], "probe_mm": rms1[key], "delta_mm": rms1[key] - rms0[key]}
               for key in keys]
    report = {
        "status": "complete", "scope": "offline field-free offset sensitivity", "offset_mm": offset_mm,
        "source_native_z_m": read_runtime_state(p0 / "handoff.json")["native_z_m"],
        "source_npz_sha256": source_sha256, "boundary": beam_record["boundary"],
        "snapshot_id": baseline["snapshot_id"], "baseline": baseline,
        "probe": {"run_dir": str(probe_dir), "entrance_particles": entrance,
                  "entrance_charge_pc": float(centroid["Charge"][1] * 1e12),
                  "end_particles": int(centroid["Particles"][-1]), "end_charge_pc": float(centroid["Charge"][-1] * 1e12),
                  "mean_flight_time_change_ps":
                      float(np.mean(shifted.payload.coordinates[:, 4] - beam.payload.coordinates[:, 4]) / C_M_S * 1e12)},
        "largest_rms_change": max(screens, key=lambda row: abs(row["delta_mm"])), "screens": screens,
    }
    report_path = directory / "report.json"
    write_runtime_state(report_path, report)
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p0-dir", type=Path, required=True, help="P0 impact-elegant-offline directory")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--offset-mm", type=float, required=True, help="Positive if ELEGANT s=0 is downstream")
    args = parser.parse_args()
    try:
        print(compare_offset(args.p0_dir.resolve(), args.output_root.resolve(), args.offset_mm))
    except (AssertionError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"Boundary sensitivity check failed. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
