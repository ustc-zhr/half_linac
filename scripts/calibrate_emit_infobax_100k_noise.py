#!/usr/bin/env python3
"""P2-B6: fresh training and held-out noise validation at the B5 setting."""

from __future__ import annotations

import argparse
import copy
import csv
import itertools
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import numpy as np
import scipy
from scripts.calibrate_emit_infobax_rms import calibration_summary
from scripts.calibrate_emit_infobax_weighted_rms import (
    read_rows, rescale, training_noise_scale, weighted_references,
)
from scripts.diagnose_emit_infobax_measurement import csv_rows, relative_error, sha256, write_json
from scripts.validate_emit_infobax_reference_precision import PrecisionBeam
from half_linac.src.apps.emit_measure.infobax_measurement import RmsVarianceConfig

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_100k_noise_calibration.json"
FROZEN = REPO_ROOT / "src/optimization/infobax/benchmarks"
PREVIOUS_STAGES = ("p2_b2", "p2_b3", "p2_b4", "p2_b4_1", "p2_b5")


def prior_particle_seeds():
    seeds = set()
    for stage in PREVIOUS_STAGES:
        folder = FROZEN / stage
        for path in folder.glob("*.csv"):
            with path.open() as handle:
                for row in csv.DictReader(handle):
                    if row.get("particle_seed"):
                        seeds.add(int(row["particle_seed"]))
        config = json.loads((folder / "config.json").read_text())
        for key, value in config.items():
            if "particle_seed" in key:
                seeds.update(value if isinstance(value, list) else [value])
    return seeds


def validate_config(config, prior_seeds):
    old = json.loads((FROZEN / "p2_b5/config.json").read_text())
    gates = json.loads((FROZEN / "p2_b4_1/config.json").read_text())["calibration_gates"]
    train, heldout = config["calibration_particle_seeds"], config["validation_particle_seeds"]
    all_seeds = train + heldout
    if (config["schema"] != "half_infobax_p2_b6_100k_noise_v1"
            or config["reference_arm"] != "native_100k" or config["particles"] != 100000
            or config["linear_resolution_factors"] != [1]
            or config["measurement_method"] != "rms_count_variance"
            or len(train) != 8 or len(heldout) != 8 or len(set(all_seeds)) != 16
            or any(type(seed) is not int or not 0 < seed < 2000000000 or seed % 2 == 0 for seed in all_seeds)
            or set(all_seeds) & prior_seeds
            or config["primary_particle_seed"] in all_seeds
            or config["calibration_gates"] != gates):
        raise ValueError("invalid B6 settings, changed gates or particle-seed leakage")
    for key in ("cases", "workpoints", "scan_values", "energy_mev", "primary_particle_seed", "measurement"):
        if config[key] != old[key]:
            raise ValueError(f"selected B5 setting changed: {key}")
    repeated = json.loads((FROZEN / "p2_b4_1/config.json").read_text())
    for key in ("repeat_workpoints", "repeat_scan_values"):
        if config[key] != repeated[key]:
            raise ValueError(f"declared noise validation grid changed: {key}")
    RmsVarianceConfig(**config["measurement"])


def unit_noise_rows(rows):
    """Remove the scale attached to each frame, preserving measured variance."""
    output = copy.deepcopy(rows)
    for row in output:
        if row["status"] == "observed":
            factor = row["measurement"]["noise_scale"]
            row["noise_std_mm2"] /= factor
            row["measurement"].update(noise_std_mm2=row["noise_std_mm2"], noise_scale=1.0)
            row["corrected_variance_mm2"] = row["variance_mm2"]
            row["corrected_variance_relative_error"] = row["variance_relative_error"]
    return output


def frames_complete(rows, config, seeds, workpoints, scan_values):
    expected = set(itertools.product(
        [case["id"] for case in config["cases"]], workpoints, scan_values, seeds,
    ))
    actual = [(r["case"], r["workpoint"], r["scan_value"], r["particle_seed"]) for r in rows]
    return (len(rows) == len(expected) and set(actual) == expected
            and all(r["status"] == "observed" for r in rows))


def reference_reproduction(references, config):
    prior = [r for r in read_rows(FROZEN / "p2_b5/references.csv")
             if r["arm"] == config["reference_arm"] and r["particle_seed"] == config["primary_particle_seed"]]
    expected = {(case["id"], work) for case in config["cases"] for work in config["workpoints"]}
    keys = [(r["case"], r["workpoint"]) for r in references]
    if len(keys) != len(expected) or set(keys) != expected:
        return dict(passed=False, maximum_relative_error=None)
    differences = []
    for row in references:
        old = next(r for r in prior if (r["case"], r["workpoint"]) == (row["case"], row["workpoint"]))
        if row["status"] != "valid" or old["status"] != "valid":
            return dict(passed=False, maximum_relative_error=None)
        differences.append(relative_error(row["emittance_mm_mrad"], old["emittance_mm_mrad"]))
    maximum = max(differences)
    return dict(passed=maximum <= 1e-8, maximum_relative_error=maximum)


def summarize(primary, training, validation, references, config):
    result = calibration_summary(primary, training, validation, references, config)
    reproduction = reference_reproduction(references, config)
    previous = json.loads((FROZEN / "p2_b5/summary.json").read_text())
    result["gates"].update(
        primary_grid_complete=frames_complete(primary, config, [config["primary_particle_seed"]],
                                               config["workpoints"], config["scan_values"]),
        training_grid_complete=frames_complete(training, config, config["calibration_particle_seeds"],
                                                config["repeat_workpoints"], config["repeat_scan_values"]),
        validation_grid_complete=frames_complete(validation, config, config["validation_particle_seeds"],
                                                  config["repeat_workpoints"], config["repeat_scan_values"]),
        selected_reference_acceptance=previous["status"] == "REFERENCE-PASS"
                                      and previous["selected_arm"] == config["reference_arm"],
        reference_reproduction=reproduction["passed"],
    )
    result["status"] = "passed" if all(result["gates"].values()) else "CALIBRATION-STOP"
    result["reference_reproduction"] = reproduction
    result["by_case_noise"] = {}
    for case in config["cases"]:
        scores = [z for row in result["noise_validation_points"] if row["case"] == case["id"]
                  for z in row["standardized_residuals"]]
        result["by_case_noise"][case["id"]] = dict(
            count=len(scores), standardized_mean=float(np.mean(scores)) if scores else None,
            standardized_mean_square=float(np.mean(np.square(scores))) if scores else None,
            interval_coverage=float(np.mean(np.abs(scores) <= 1.959963984540054)) if scores else None,
        )
    return result


def collect(config, output, label, seeds):
    rows, projections, attempts = [], {}, 0
    for case in config["cases"]:
        with TemporaryDirectory(prefix="infobax-b6-") as temporary:
            beam = PrecisionBeam(Path(temporary), config, case, config["particles"], 1.0)
            projections[case["id"]] = beam.projections
            for work in config["repeat_workpoints"]:
                for scan in config["repeat_scan_values"]:
                    for seed in seeds:
                        rows.extend(beam.measure_pair(work, scan, seed))
                    csv_rows(output / f"{label}_raw.csv", rows)
                    print(f"B6 {label} case={case['id']} workpoint={work} scan={scan} recorded", flush=True)
            attempts += beam.attempts
    return unit_noise_rows(rows), projections, attempts


def run(config, output):
    primary = unit_noise_rows([r for r in read_rows(FROZEN / "p2_b5/queries.csv")
                              if r["arm"] == config["reference_arm"]
                              and r["particle_seed"] == config["primary_particle_seed"]])
    training, projections, attempts = collect(config, output, "training", config["calibration_particle_seeds"])
    try:
        if not frames_complete(training, config, config["calibration_particle_seeds"],
                               config["repeat_workpoints"], config["repeat_scan_values"]):
            raise ValueError("incomplete or invalid training grid; no noise-scale fit")
        fit = training_noise_scale(training, config)
        fit.update(data_source="P2-B6 fresh 100k training populations only; no validation observations",
                   particle_seeds=config["calibration_particle_seeds"], particles=config["particles"])
    except ValueError as error:
        summary = dict(stage="P2-B6", status="CALIBRATION-STOP", noise_fit_error=str(error),
                       new_tracking_attempts=attempts, validation_started=False, optimizer_runs=0,
                       failures=[r for r in training if r["status"] != "observed"], ioc_access=False)
        write_json(output / "summary.json", summary)
        print(json.dumps(summary, indent=2), flush=True)
        return
    # This file is written before collect() starts any held-out population.
    write_json(output / "noise_scale_fit.json", fit)
    print(f"B6 training-only noise scale frozen: {fit['noise_scale']}", flush=True)
    primary, training = rescale(primary, fit["noise_scale"]), rescale(training, fit["noise_scale"])
    references = []
    for case in config["cases"]:
        references.extend(weighted_references([r for r in primary if r["case"] == case["id"]],
                                              projections[case["id"]], config))
    for name, rows in (("primary.csv", primary), ("calibration.csv", training), ("references.csv", references)):
        csv_rows(output / name, rows)
    validation, _, validation_attempts = collect(config, output, "validation", config["validation_particle_seeds"])
    validation = rescale(validation, fit["noise_scale"])
    csv_rows(output / "validation.csv", validation)
    calibration = summarize(primary, training, validation, references, config)
    write_json(output / "calibration_summary.json", calibration)
    summary = dict(stage="P2-B6", status="NOISE-PASS" if calibration["status"] == "passed" else "CALIBRATION-STOP",
                   reference_arm=config["reference_arm"], noise_scale=fit["noise_scale"],
                   calibration_gates=calibration["gates"], new_tracking_attempts=attempts+validation_attempts,
                   reused_primary_frames=len(primary), reused_reference_validation="B5 native_100k, 42/42 accepted references",
                   validation_started=True, optimizer_runs=0, ioc_access=False, gp_or_acquisition_changes=False,
                   runtime_acceptance_at_100k="pending", future_query_noise="existing fixed forecast; unvalidated by this stage",
                   gui_acceptance="not established by offline particle-noise validation")
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_bytes())
    validate_config(config, prior_particle_seeds())
    if config["predecessor_config_sha256"] != sha256(FROZEN / "p2_b5/config.json"):
        raise ValueError("B5 configuration mismatch")
    for stage in PREVIOUS_STAGES:
        folder = FROZEN / stage
        index = json.loads((folder / "result_index.json").read_text())
        for name, digest in index.get("artifacts_sha256", {}).items():
            if sha256(folder / name) != digest:
                raise ValueError(f"predecessor artifact mismatch: {stage}/{name}")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.config, args.output_dir / "config.json")
    source_names = ["scripts/calibrate_emit_infobax_100k_noise.py", "scripts/validate_emit_infobax_reference_precision.py",
                    "scripts/calibrate_emit_infobax_weighted_rms.py", "scripts/calibrate_emit_infobax_rms.py",
                    "scripts/diagnose_emit_infobax_measurement.py", "scripts/check_emit_infobax_vm.py",
                    "src/apps/emit_measure/infobax_measurement.py", "src/shared/beam_matrix.py",
                    "src/shared/elegant_backend/parser.py", "src/shared/machine_profile/model_backend.py",
                    "src/shared/runtime_state.py", "src/virtual_machine/common/start_VM.py",
                    "src/virtual_machine/workbench_data.py"]
    prior = {str(p.relative_to(REPO_ROOT)): sha256(p) for stage in PREVIOUS_STAGES
             for p in sorted((FROZEN / stage).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    sources = {name: sha256(REPO_ROOT / name) for name in source_names}
    write_json(args.output_dir / "metadata.json", dict(
        config_sha256=sha256(args.config), source_sha256=sources, predecessor_evidence_sha256=prior,
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
        elegant_binary_sha256=sha256(shutil.which("elegant")), excluded_prior_particle_seeds=sorted(prior_particle_seeds()),
    ))
    run(config, args.output_dir)
    if any(sha256(REPO_ROOT / name) != digest for name, digest in {**sources, **prior}.items()):
        raise RuntimeError("frozen source or predecessor evidence changed")


if __name__ == "__main__":
    main()
