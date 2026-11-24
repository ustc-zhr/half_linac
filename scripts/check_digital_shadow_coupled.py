#!/usr/bin/env python3
"""Recheck saved HALF Digital Shadow P0 and P1 coupled jobs offline."""

import argparse
import json
from math import isfinite
from pathlib import Path
import sys

from check_digital_shadow_p0 import ROOT, RMS_TOLERANCE_MM, SCREENS, _columns, _require, check_run
from half_linac.src.apps.digital_shadow.jobs import read_archive
from half_linac.src.shared.digital_shadow.beam_state import load_beam_state_archive
from half_linac.src.shared.digital_shadow.impact_model import DECK, IMPACT_L01B_PROBE, IMPACT_NOMINAL
from half_linac.src.shared.digital_shadow.session import file_digest
from half_linac.src.shared.elegant_backend.parser import _load_watch_rms_from_sdds
from half_linac.src.shared.runtime_state import read_runtime_state


BASELINE = ROOT / "configs/machines/half/benchmarks/digital_shadow_coupled_acceptance.json"
HANDOFF = {"physical_screen_id": 264, "rf_end_z_m": 9.1994809, "native_z_m": 9.2994809,
           "drift_in_impact_m": 0.1, "extra_drift_m": 0}


def _check_coupled_archive(path, variant, deck_sha256, baseline):
    archive = read_archive(path, verify_particle_inputs=True)
    manifest, request, result = archive["manifest"], archive["request"], archive["result"]
    _require(archive["status"] == "complete" and archive["backend"] == "offline"
             and all(record and record.get("scope") == "coupled" for record in (manifest, request, result)),
             f"Expected a complete offline Coupled archive at {path}")
    _require(all(record.get("impact_variant", IMPACT_NOMINAL) == variant for record in (manifest, request, result)),
             f"Expected upstream case {variant} at {path}")

    run = Path(archive["run_dir"])
    upstream, downstream = run / "upstream", run / "downstream"
    _require(all(manifest["stages"][name]["status"] == "complete" for name in ("upstream", "downstream")),
             f"Both Coupled stages must be complete at {path}")
    _require(file_digest(upstream / "impact" / DECK) == deck_sha256
             and all(record.get("deck_sha256", deck_sha256) == deck_sha256 for record in (manifest, result)),
             f"{variant} input deck differs from the accepted case")
    _require(file_digest(upstream / "impact/fort.84") == baseline["l01a_exit_sha256"],
             f"{variant} L01A exit particles differ from the accepted input to L01B")

    handoff = manifest["impact_handoff"]
    _require(all(handoff.get(key) == value for key, value in HANDOFF.items())
             and handoff == read_runtime_state(upstream / "handoff.json")
             == read_runtime_state(downstream / "inputs/impact-handoff.json"),
             f"{variant} must use the archived native plane and zero extra drift")
    beam, beam_record = load_beam_state_archive(upstream / "entrance.json")
    tracked = read_runtime_state(downstream / "manifest.json")
    _require(beam_record["data"]["sha256"] == tracked["particle_input"]["source_npz_sha256"]
             and beam_record["boundary"] == tracked["boundary"],
             f"{variant} ELEGANT input does not match this job's upstream beam")
    snapshot = archive["snapshot"]
    _require(snapshot == read_runtime_state(downstream / "machine_snapshot.json")
             and manifest["snapshot_id"] == tracked["snapshot_id"] == baseline["snapshot_id"],
             f"{variant} must use the accepted VM snapshot for both the job and ELEGANT")
    _require(result["predictions"] == read_runtime_state(downstream / "result.json")["predictions"],
             f"{variant} job predictions differ from its archived ELEGANT stage")
    stats = result["beam_statistics"]
    _require(stats == manifest["beam_statistics"] and stats["particles"] == len(beam.payload.coordinates)
             and stats["charge_pc"] == -beam.bunch_charge_c * 1e12
             and stats["p_ref_mev_c"] == beam.reference.p_ref_mev_c,
             f"{variant} reported beam statistics differ from the upstream particle data")
    return archive, downstream


def _check_probe(archive, downstream, state, expected):
    manifest = read_runtime_state(downstream / "manifest.json")
    beam, record = load_beam_state_archive(downstream / "inputs/entrance.json")
    particle_input = manifest["particle_input"]
    _require(record["data"]["sha256"] == particle_input["source_npz_sha256"] == expected["source_npz_sha256"],
             "P1 entrance particles differ from the independent P1 reference")
    _require(file_digest(downstream / "inputs/input.sdds") == particle_input["sdds_sha256"] == expected["input_sdds_sha256"],
             "P1 entrance SDDS differs from the independent P1 reference")
    centroid = _columns(downstream / "elegant/tracking.cen")
    entrance_count, end_count = map(int, centroid["Particles"][[0, -1]])
    entrance_charge, end_charge = map(float, centroid["Charge"][[1, -1]] * 1e12)
    momentum = beam.reference.p_ref_mev_c
    _require(entrance_count == expected["entrance_particles"]
             and entrance_charge == -beam.bunch_charge_c * 1e12 == expected["entrance_charge_pc"]
             and momentum == float(state["control"]["run_setup"]["p_central_mev"]) == expected["entrance_reference_p_mev_c"],
             "P1 entrance count, charge or reference momentum differs from the independent reference")
    _require(end_count == archive["result"]["end_particles"] == expected["end_particles"]
             and end_charge == expected["end_charge_pc"], "P1 end-of-line count or charge differs from the independent reference")

    rows = archive["result"]["predictions"]
    predictions = {row["key"]: row["predicted"] for row in rows}
    reference = expected["rms_mm"]
    keys = {f"{screen}/sig{axis}" for screen in SCREENS for axis in "xy"}
    _require(predictions.keys() == reference.keys() == keys and len(rows) == len(keys), "Expected 26 P1 RMS predictions")
    residuals = []
    for screen in SCREENS:
        watch = state["lattice"][screen]["FILENAME"].strip('"')
        for axis, rms in zip("xy", _load_watch_rms_from_sdds(downstream / "elegant" / watch)):
            key = f"{screen}/sig{axis}"
            _require(isfinite(reference[key]) and abs(rms - predictions[key]) <= RMS_TOLERANCE_MM,
                     f"P1 {key} disagrees with the archived WATCH output")
            residuals.append({"key": key, "rms_mm": rms, "reference_mm": reference[key],
                              "residual_mm": rms - reference[key]})
    maximum = max(abs(row["residual_mm"]) for row in residuals)
    return {"status": "passed" if maximum <= RMS_TOLERANCE_MM else "failed",
            "run_dir": archive["run_dir"], "deck_sha256": expected["deck_sha256"],
            "reference_report_sha256": expected["source_report_sha256"], "entrance_particles": entrance_count,
            "entrance_charge_pc": entrance_charge, "entrance_reference_p_mev_c": momentum,
            "end_particles": end_count, "end_charge_pc": end_charge,
            "max_abs_screen_residual_mm": maximum, "screen_residuals": residuals}


def check_coupled(p0_run, p1_run, reference_dir):
    baseline = read_runtime_state(BASELINE)
    _require(baseline["schema"] == "digital_shadow_coupled_acceptance_v1", "Unsupported Coupled acceptance reference")
    p0, nominal = _check_coupled_archive(p0_run, IMPACT_NOMINAL, baseline["p0_deck_sha256"], baseline)
    p1, probe = _check_coupled_archive(p1_run, IMPACT_L01B_PROBE, baseline["p1"]["deck_sha256"], baseline)
    nominal_state = read_runtime_state(nominal / "inputs/evaluated_state.json")
    probe_state = read_runtime_state(probe / "inputs/evaluated_state.json")
    _require(p0["snapshot"] == p1["snapshot"] and all(nominal_state[key] == probe_state[key] for key in ("lattice", "usedline"))
             and all(file_digest(nominal / "inputs" / name) == file_digest(probe / "inputs" / name)
                     for name in ("source.lte", "tracking-template.ele")),
             "P0 and P1 must use the same VM snapshot and downstream lattice settings")
    nominal_report = check_run(nominal, reference_dir)
    _require(nominal_report["acceptance_csv_sha256"] == baseline["p0_acceptance_csv_sha256"],
             "P0 acceptance CSV differs from the reference baseline")
    _require(nominal_report["end_particles"] == p0["result"]["end_particles"], "P0 end particle count differs from its job result")
    nominal_report.update(coupled_run_dir=p0["run_dir"], deck_sha256=baseline["p0_deck_sha256"])
    probe_report = _check_probe(p1, probe, probe_state, baseline["p1"])
    nominal_rms = {row["key"]: row["predicted"] for row in p0["result"]["predictions"]}
    changes = [{"key": row["key"], "probe_minus_nominal_mm": row["rms_mm"] - nominal_rms[row["key"]]}
               for row in probe_report["screen_residuals"]]
    return {"status": "passed" if nominal_report["status"] == probe_report["status"] == "passed" else "failed",
            "snapshot_id": baseline["snapshot_id"], "quadrupoles_verified": nominal_report["quadrupoles_verified"],
            "handoff": HANDOFF, "l01a_exit_sha256": baseline["l01a_exit_sha256"], "tolerance_mm": RMS_TOLERANCE_MM,
            "reference_momentum_increase_mev_c": probe_report["entrance_reference_p_mev_c"] - nominal_report["entrance_reference_p_mev_c"],
            "largest_rms_change": max(changes, key=lambda row: abs(row["probe_minus_nominal_mm"])),
            "p0": nominal_report, "p1": probe_report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p0-run", type=Path, required=True, help="P0 Coupled GUI job or model archive directory")
    parser.add_argument("--p1-run", type=Path, required=True, help="P1 Coupled GUI job or model archive directory")
    parser.add_argument("--reference-dir", type=Path, required=True, help="P0 vm-same-working-point reference directory")
    args = parser.parse_args()
    try:
        report = check_coupled(args.p0_run, args.p1_run, args.reference_dir)
    except (IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"Coupled regression failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
