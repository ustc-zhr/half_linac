#!/usr/bin/env python3
"""Compare an offline HALF particle run with the saved P0 isolated VM acceptance."""

import argparse
import csv
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.apps.digital_shadow.jobs import read_archive
from half_linac.src.shared.digital_shadow.beam_state import load_beam_state_archive
from half_linac.src.shared.digital_shadow.session import file_digest
from half_linac.src.shared.elegant_backend.parser import _new_legacy_sdds_dataset
from half_linac.src.shared.runtime_state import read_runtime_state


RMS_TOLERANCE_MM = 1e-12
SCREENS = tuple(f"PRF{index:02d}" for index in range(2, 15))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _columns(path):
    dataset = _new_legacy_sdds_dataset()
    dataset.load(str(path))
    return {name: np.asarray(pages[-1]) for name, pages in zip(dataset.columnName, dataset.columnData)}


def check_run(run, reference):
    archive = read_archive(run, verify_particle_inputs=True)
    _require(archive["status"] == "complete" and archive["backend"] == "offline", "Expected a complete offline run")
    run = Path(archive["run_dir"])
    manifest = archive["manifest"]
    expected = read_runtime_state(reference / "comparison.json")
    capture = expected["vm_capture"]
    snapshot = read_runtime_state(reference / "vm-machine-snapshot.json")
    _require(archive["snapshot"] == snapshot and manifest["snapshot_id"] == capture["snapshot_id"],
             "VM snapshot differs from the acceptance capture")

    beam, beam_record = load_beam_state_archive(run / "inputs/entrance.json")
    particle_input = manifest["particle_input"]
    source_beam = read_runtime_state(reference.parent / "impact-native-entrance.json")
    _require(particle_input["source_npz_sha256"] == source_beam["data"]["sha256"],
             "Frozen particle NPZ hash differs from acceptance")
    _require(file_digest(run / "inputs/input.sdds") == particle_input["sdds_sha256"] == capture["incoming_sdds_sha256"],
             "Entrance SDDS differs from the isolated VM input")
    _require(manifest["boundary"] == beam_record["boundary"] == source_beam["boundary"]
             and file_digest(run / "inputs/source.lte") == beam.boundary.lattice_sha256 == capture["source_lattice_sha256"],
             "Source lattice differs from the isolated VM lattice")
    handoff = read_runtime_state(run / "inputs/impact-handoff.json")
    expected_handoff = read_runtime_state(reference.parent / "handoff.json")
    _require(handoff == manifest["impact_handoff"]
             and all(handoff.get(key) == value for key, value in expected_handoff.items() if key != "source"),
             "Native-plane handoff differs from acceptance")

    state = read_runtime_state(run / "inputs/evaluated_state.json")
    lattice = state["lattice"]
    _require(state["usedline"][:5] == ["C", "MALIN", "CCC", "W00", "DHOTGUN0A"]
             and float(lattice["DHOTGUN0A"]["L"]) == 0.25 and float(lattice["ES01"]["X_MAX"]) == 0.004,
             "Entrance sequence, first drift or ES01 design aperture differs")
    k1 = {entry["element_id"]: entry["value"] for entry in snapshot["entries"] if entry["logical_channel"] == "K1"}
    applied = {name: float(lattice[name]["K1"]) for name in state["usedline"] if lattice[name]["TYPE"].upper() == "QUAD"}
    _require(len(k1) == expected["quadrupoles_verified"] == 63 and applied == k1,
             "The 63 evaluated QUAD K1 values must match the saved VM settings")

    centroid = _columns(run / "elegant/tracking.cen")
    vm_centroid = _columns(reference / "vm/one.cen")
    for column in ("ElementName", "ElementOccurence", "s", "Particles", "Charge", "pCentral"):
        _require(np.array_equal(centroid[column], vm_centroid[column]), f"Tracking column {column} differs from the VM")
    momentum = expected["entrance_reference_p_MeV_c"]
    _require(beam.reference.p_ref_mev_c == float(state["control"]["run_setup"]["p_central_mev"]) == momentum,
             "Entrance reference momentum differs")
    entrance_count, end_count = map(int, centroid["Particles"][[0, -1]])
    entrance_charge, end_charge = map(float, centroid["Charge"][[1, -1]] * 1e12)
    _require(entrance_count == len(beam.payload.coordinates) == expected["entrance_particles"]
             and entrance_charge == -beam.bunch_charge_c * 1e12 == expected["entrance_charge_pC"]
             and float(lattice["C"]["TOTAL"]) == -beam.bunch_charge_c, "Entrance count or charge differs")
    _require(end_count == expected["end_particles"] == 25263 and end_charge == expected["end_charge_pC"],
             "End-of-line count or charge differs")

    csv_path = reference / "screen-comparison.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        screens = list(csv.DictReader(stream))
    predictions = {row["key"]: row["predicted"] for row in archive["result"]["predictions"]}
    keys = {f"{screen}/sig{axis}" for screen in SCREENS for axis in "xy"}
    _require(tuple(row["screen"] for row in screens) == SCREENS and predictions.keys() == keys
             and len(archive["result"]["predictions"]) == len(keys), "Expected 26 predictions across PRF02–PRF14")
    residuals = []
    for row in screens:
        screen = row["screen"]
        watch = _columns(run / "elegant" / lattice[screen]["FILENAME"].strip('"'))
        vm_watch = _columns(reference / "vm" / f"{screen}.out")
        _require(len(watch["particleID"]) == int(row["particles"]), f"{screen} particle count differs")
        for column in ("x", "xp", "y", "yp", "t", "p", "particleID"):
            _require(np.array_equal(watch[column], vm_watch[column]), f"{screen} WATCH column {column} differs")
        for axis in "xy":
            vm_rms = float(row[f"vm_sigma_{axis}_mm"])
            pred = predictions[f"{screen}/sig{axis}"]
            _require(np.isfinite(vm_rms) and np.isfinite(pred), f"{screen} {axis} RMS must be finite")
            residuals.append({"screen": screen, "axis": axis, "vm_source": row[f"vm_source_{axis}"],
                              "vm_rms_mm": vm_rms, "shadow_rms_mm": pred, "vm_minus_shadow_mm": vm_rms - pred})

    maximum = max(abs(row["vm_minus_shadow_mm"]) for row in residuals)
    return {
        "status": "passed" if maximum <= RMS_TOLERANCE_MM else "failed",
        "run_dir": str(run), "snapshot_id": capture["snapshot_id"], "quadrupoles_verified": len(k1),
        "source_npz_sha256": particle_input["source_npz_sha256"],
        "input_sdds_sha256": capture["incoming_sdds_sha256"], "acceptance_csv_sha256": file_digest(csv_path),
        "entrance_particles": entrance_count, "entrance_reference_p_mev_c": momentum,
        "entrance_charge_pc": entrance_charge, "end_particles": end_count, "end_charge_pc": end_charge,
        "same_native_watch_coordinates_and_ids": True, "tolerance_mm": RMS_TOLERANCE_MM,
        "max_abs_screen_residual_mm": maximum, "screen_residuals": residuals,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True, help="P0 vm-same-working-point directory")
    args = parser.parse_args()
    try:
        report = check_run(args.run, args.reference_dir)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"P0 regression failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
