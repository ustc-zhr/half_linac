#!/usr/bin/env python3
"""Recompute and compare the HALF P0 injector working point offline."""

import argparse
from dataclasses import replace
from pathlib import Path
import sys
from uuid import uuid4

import numpy as np

from check_digital_shadow_p0 import SCREENS, _columns, _require, check_run
from half_linac.src.shared.digital_shadow.beam_state import load_beam_state, save_beam_state
from half_linac.src.shared.digital_shadow.impact_model import (
    DECK, IMPACT_L01B_PROBE as PROBE, IMPACT_NOMINAL as NOMINAL, WORKING_POINT, beam_at_boundary, beam_stats,
    ensure_impact_finished, l01b_probe_deck, run_impact, stage_impact, verify_impact_inputs,
)
from half_linac.src.shared.digital_shadow.particle_model import ParticleModelRequest, run_particle_model
from half_linac.src.shared.digital_shadow.session import file_digest
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state


def _run_impact(benchmark, directory, variant, library_dir, working_point):
    impact = stage_impact(benchmark, directory, working_point, variant=variant)
    print(impact, flush=True)
    run_impact(directory, library_dir)


def _track(context, directory, beam, handoff, snapshot):
    beam_path = save_beam_state(beam, directory / "entrance.json")
    handoff_path = directory / "handoff.json"
    write_runtime_state(handoff_path, handoff)
    request = ParticleModelRequest(beam_source="archived", beam_path=str(beam_path),
                                   parameter_source="snapshot", snapshot_path=str(snapshot),
                                   handoff_path=str(handoff_path), model_timeout_s=300)
    write_runtime_state(directory / "request.json", request.as_metadata())
    result = run_particle_model(context, request, directory / "shadow-runs")
    _require(result["status"] == "complete", f"Incomplete downstream tracking in {directory}")
    return result


def _compare(benchmark, root, working_point):
    reference = benchmark / "comparison/impact-elegant-offline"
    vm_reference = reference / "vm-same-working-point"
    snapshot = vm_reference / "vm-machine-snapshot.json"
    _require(read_runtime_state(snapshot)["snapshot_id"] == working_point["vm_snapshot_id"],
             "VM snapshot differs from the working point")
    frozen = load_beam_state(reference / "impact-native-entrance.json")
    handoff = read_runtime_state(reference / "handoff.json")
    nominal_impact, probe_impact = (root / variant / "impact" for variant in (NOMINAL, PROBE))
    for impact in (nominal_impact, probe_impact):
        ensure_impact_finished(impact)
    verify_impact_inputs(nominal_impact.parent, working_point)
    expected_deck = l01b_probe_deck((nominal_impact / DECK).read_text(), working_point["l01b_probe"])
    verify_impact_inputs(probe_impact.parent, working_point, expected_deck=expected_deck)
    before_l01b = file_digest(nominal_impact / "fort.84")
    _require(before_l01b == file_digest(probe_impact / "fort.84"),
             "Particles already differ at the L01A exit, upstream of the L01B perturbation")

    both = {}
    context = load_model_context("half")
    directory = root / f"comparison-{uuid4().hex}"
    for variant, impact in ((NOMINAL, nominal_impact), (PROBE, probe_impact)):
        detail = f"{frozen.provenance.detail} P1 working point {working_point['id']}, variant {variant}."
        beam, metadata = beam_at_boundary(impact, handoff, frozen, detail)
        metadata["upstream"] = {"working_point": working_point, "variant": variant,
                                "deck_sha256": file_digest(impact / DECK)}
        if variant == NOMINAL:
            _require(np.array_equal(frozen.payload.particle_ids, beam.payload.particle_ids)
                     and np.array_equal(frozen.payload.coordinates, beam.payload.coordinates),
                     "Recomputed nominal 6D particles differ from the frozen P0 beam")
        result = _track(context, directory / variant, beam, metadata, snapshot)
        if variant == NOMINAL:
            baseline = check_run(result["run_dir"], vm_reference)
            _require(baseline["status"] == "passed", "Recomputed nominal model did not pass the P0 regression")
        both[variant] = (beam, result)
    nominal, nominal_result = both[NOMINAL]
    probe, probe_result = both[PROBE]
    nominal_dir, probe_dir = (Path(result["run_dir"]) for result in (nominal_result, probe_result))
    _require(read_runtime_state(nominal_dir / "model_snapshot.json")
             == read_runtime_state(probe_dir / "model_snapshot.json"), "Downstream VM settings differ between runs")
    common, ni, pi = np.intersect1d(nominal.payload.particle_ids, probe.payload.particle_ids, return_indices=True)
    _require(len(common) > 0, "No shared particles in the two native-plane gates")
    delta_p = ((1 + probe.payload.coordinates[pi, 5]) * probe.reference.p_ref_mev_c
               - (1 + nominal.payload.coordinates[ni, 5]) * nominal.reference.p_ref_mev_c)
    mean_delta_p = float(delta_p.mean())
    _require(mean_delta_p > 0, "Increasing the L01B RF field did not increase matched-particle mean momentum")
    nominal_rms, probe_rms = ({row["key"]: row["predicted"] for row in result["predictions"]}
                             for result in (nominal_result, probe_result))
    keys = [f"{name}/sig{axis}" for name in SCREENS for axis in "xy"]
    _require(nominal_rms.keys() == probe_rms.keys() == set(keys), "Expected 26 screen RMS values in both runs")
    rows = [{"key": key, "nominal_mm": nominal_rms[key], "probe_mm": probe_rms[key],
             "probe_minus_nominal_mm": probe_rms[key] - nominal_rms[key]} for key in keys]
    largest = max(rows, key=lambda row: abs(row["probe_minus_nominal_mm"]))
    _require(abs(largest["probe_minus_nominal_mm"]) > baseline["tolerance_mm"],
             "No downstream screen response above the nominal regression tolerance")

    centroid = _columns(probe_dir / "elegant/tracking.cen")
    report = {
        "status": "passed", "working_point": working_point,
        "l01a_exit_sha256_identical": before_l01b,
        "frozen_entrance": beam_stats(frozen), "nominal_entrance": beam_stats(nominal),
        "probe_entrance": beam_stats(probe), "common_selected_particles": len(common),
        "common_mean_delta_p_mev_c": mean_delta_p,
        "probe_downstream": {"run_dir": str(probe_dir), "end_particles": int(centroid["Particles"][-1]),
                             "end_charge_pc": float(centroid["Charge"][-1] * 1e12)},
        "nominal_vm_regression": baseline, "largest_rms_change": largest, "screen_changes": rows,
    }
    report_path = directory / "report.json"
    write_runtime_state(report_path, report)
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True, help="P0 final-benchmark directory")
    parser.add_argument("--run-dir", type=Path, required=True, help="Directory for this nominal and probe pair")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run-impact", help="Recompute one IMPACT-T case from its initial particles")
    run.add_argument("variant", choices=(NOMINAL, PROBE))
    run.add_argument("--impact-lib-dir", type=Path, required=True)
    commands.add_parser("compare", help="Track both recomputed bunches in ELEGANT and archive the comparison")
    args = parser.parse_args()
    try:
        working_point = read_runtime_state(WORKING_POINT)
        benchmark, root = args.benchmark.resolve(), args.run_dir.resolve()
        if args.command == "run-impact":
            _run_impact(benchmark, root / args.variant, args.variant, args.impact_lib_dir.resolve(), working_point)
        else:
            print(_compare(benchmark, root, working_point))
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"P1 offline run failed. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
