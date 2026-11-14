#!/usr/bin/env python3
"""P2-B7: fixed expanded seed budget and whole-seed noise uncertainty."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
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
from scripts import calibrate_emit_infobax_100k_noise as b6
from scripts.calibrate_emit_infobax_weighted_rms import read_rows, rescale, training_noise_scale, weighted_references
from scripts.diagnose_emit_infobax_measurement import csv_rows, sha256, write_json
from scripts.validate_emit_infobax_reference_precision import PrecisionBeam

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_expanded_noise_calibration.json"
FROZEN = b6.FROZEN
PREVIOUS_STAGES = (*b6.PREVIOUS_STAGES, "p2_b6")


def prior_particle_seeds():
    seeds = b6.prior_particle_seeds()
    config = json.loads((FROZEN / "p2_b6/config.json").read_text())
    seeds.update(config["calibration_particle_seeds"] + config["validation_particle_seeds"])
    return seeds


def validate_config(config, prior_seeds):
    train, heldout = config["calibration_particle_seeds"], config["validation_particle_seeds"]
    seeds = train + heldout
    bootstrap = config["seed_bootstrap"]
    if (config["schema"] != "half_infobax_p2_b7_expanded_noise_v1" or config["stage"] != "P2-B7"
            or len(train) != 32 or len(heldout) != 32 or len(set(seeds)) != 64
            or set(seeds) & prior_seeds
            or any(type(s) is not int or not 0 < s < 2000000000 or s % 2 == 0 for s in seeds)
            or type(config["workers"]) is not int or not 1 <= config["workers"] <= 3
            or bootstrap["samples"] != 5000 or bootstrap["confidence"] != 0.95
            or bootstrap["training_rng_seed"] != 2026111317 or bootstrap["validation_rng_seed"] != 2026111327):
        raise ValueError("invalid expanded budget, seed leakage or bootstrap configuration")
    # Reuse the unchanged B6 physical settings and gate checks, independently
    # of B7's larger seed-count contract checked above.
    b6.validate_config(dict(config, schema="half_infobax_p2_b6_100k_noise_v1",
                            calibration_particle_seeds=train[:8], validation_particle_seeds=heldout[:8]), prior_seeds)


def point_keys(config):
    return [(case["id"], work, scan) for case in config["cases"]
            for work in config["repeat_workpoints"] for scan in config["repeat_scan_values"]]


def frame_matrices(rows, config, seeds):
    if not b6.frames_complete(rows, config, seeds, config["repeat_workpoints"], config["repeat_scan_values"]):
        raise ValueError("incomplete seed vectors; no uncertainty estimate from a filtered subset")
    indexed = {(r["particle_seed"], r["case"], r["workpoint"], r["scan_value"]): r for r in rows}
    values = np.array([[indexed[(seed, *point)]["corrected_variance_mm2"] for point in point_keys(config)] for seed in seeds])
    errors = np.array([[indexed[(seed, *point)]["noise_std_mm2"] for point in point_keys(config)] for seed in seeds])
    return values, errors


def percentile_interval(values, confidence):
    tail = (1-confidence)/2
    return np.quantile(values, [tail, 1-tail]).tolist()


def training_seed_bootstrap(training, config):
    values, errors = frame_matrices(training, config, config["calibration_particle_seeds"])
    options = config["seed_bootstrap"]
    rng = np.random.default_rng(options["training_rng_seed"])
    draws = rng.integers(len(values), size=(options["samples"], len(values)))
    estimates = []
    for block in np.array_split(draws, 20):
        empirical = np.var(values[block], axis=1, ddof=1)
        predicted = np.mean(errors[block]**2, axis=1)
        estimates.extend(np.sqrt(np.mean(empirical/predicted, axis=1)).tolist())
    return dict(noise_scale_interval=percentile_interval(estimates, options["confidence"]),
                samples=options["samples"], seed_count=len(values),
                unit="complete training seed vector across all optical points")


def seed_score_statistics(scores, options):
    scores = np.asarray(scores, dtype=float)
    if scores.ndim != 2 or len(scores) < 2 or scores.shape[1] < 1 or not np.all(np.isfinite(scores)):
        raise ValueError("at least two complete finite seed vectors are required")
    coverage = np.mean(np.abs(scores) <= 1.959963984540054, axis=1)
    mean, mean_square = np.mean(scores, axis=1), np.mean(scores**2, axis=1)
    rng = np.random.default_rng(options["validation_rng_seed"])
    draws = rng.integers(len(scores), size=(options["samples"], len(scores)))
    output = dict(seed_count=len(scores), points_per_seed=scores.shape[1], samples=options["samples"],
                  resampling_unit="whole validation seed vector; optical points stay together",
                  interpretation="Conditional on frozen training fit and means; training-fit uncertainty excluded; descriptive only")
    for key, values in (("coverage", coverage), ("standardized_mean", mean), ("standardized_mean_square", mean_square)):
        output[key] = dict(estimate=float(np.mean(values)),
                           seed_bootstrap_interval=percentile_interval(np.mean(values[draws], axis=1), options["confidence"]))
    return output


def validation_seed_diagnostics(training, validation, config, output):
    values, errors = frame_matrices(training, config, config["calibration_particle_seeds"])
    heldout, heldout_errors = frame_matrices(validation, config, config["validation_particle_seeds"])
    center = values.mean(axis=0)
    mean_variance = np.sum(errors**2, axis=0)/len(values)**2
    scores = (heldout-center)/np.sqrt(heldout_errors**2+mean_variance)
    rows, seeds = [], []
    for i, seed in enumerate(config["validation_particle_seeds"]):
        z = scores[i]
        seeds.append(dict(particle_seed=seed, points=len(z), outside_95_interval=int(sum(abs(z)>1.959963984540054)),
                          standardized_mean=float(np.mean(z)), standardized_mean_square=float(np.mean(z*z)),
                          minimum_z=float(min(z)), maximum_z=float(max(z))))
        for j, (case, work, scan) in enumerate(point_keys(config)):
            rows.append(dict(particle_seed=seed, case=case, workpoint=work, scan_value=scan,
                             standardized_residual=float(z[j]), outside_95_interval=bool(abs(z[j])>1.959963984540054)))
    csv_rows(output / "validation_scores.csv", rows)
    csv_rows(output / "validation_seeds.csv", seeds)
    return seed_score_statistics(scores, config["seed_bootstrap"])


def collect_case(config, case, label, seeds):
    rows = []
    with TemporaryDirectory(prefix="infobax-b7-") as temporary:
        beam = PrecisionBeam(Path(temporary), config, case, config["particles"], 1.0)
        for work in config["repeat_workpoints"]:
            for scan in config["repeat_scan_values"]:
                for seed in seeds:
                    rows.extend(beam.measure_pair(work, scan, seed))
                print(f"B7 {label} case={case['id']} workpoint={work} scan={scan} recorded", flush=True)
        return rows, beam.projections, beam.attempts


def collect(config, output, label, seeds):
    by_case, projections, attempts = {}, {}, 0
    # Separate processes give each SDDS instance and model a private workspace.
    with ProcessPoolExecutor(max_workers=config["workers"], mp_context=multiprocessing.get_context("spawn")) as executor:
        jobs = {executor.submit(collect_case, config, case, label, seeds): case["id"] for case in config["cases"]}
        for future in as_completed(jobs):
            case = jobs[future]
            rows, projection, actual = future.result()
            by_case[case], projections[case] = rows, projection
            attempts += actual
            ordered = [row for case in config["cases"] for row in by_case.get(case["id"], [])]
            csv_rows(output / f"{label}_raw.csv", ordered)
    return b6.unit_noise_rows(ordered), projections, attempts


def run(config, output):
    primary = b6.unit_noise_rows([r for r in read_rows(FROZEN / "p2_b5/queries.csv")
                                 if r["arm"] == config["reference_arm"] and r["particle_seed"] == config["primary_particle_seed"]])
    training, projections, attempts = collect(config, output, "training", config["calibration_particle_seeds"])
    try:
        if not b6.frames_complete(training, config, config["calibration_particle_seeds"], config["repeat_workpoints"], config["repeat_scan_values"]):
            raise ValueError("invalid or incomplete training populations; no fit")
        fit = training_noise_scale(training, config)
        fit.update(data_source="P2-B7 fresh 32-seed training set only; no held-out observations",
                   particle_seeds=config["calibration_particle_seeds"], particles=config["particles"],
                   seed_bootstrap=training_seed_bootstrap(training, config))
    except ValueError as error:
        write_json(output / "summary.json", dict(stage="P2-B7", status="CALIBRATION-STOP", noise_fit_error=str(error),
                   new_tracking_attempts=attempts, validation_started=False, optimizer_runs=0, ioc_access=False))
        return
    write_json(output / "noise_scale_fit.json", fit)
    print(f"B7 training-only noise scale frozen: {fit['noise_scale']}", flush=True)
    primary, training = rescale(primary, fit["noise_scale"]), rescale(training, fit["noise_scale"])
    references = [ref for case in config["cases"] for ref in weighted_references(
        [r for r in primary if r["case"] == case["id"]], projections[case["id"]], config)]
    for name, rows in (("primary.csv", primary), ("calibration.csv", training), ("references.csv", references)):
        csv_rows(output / name, rows)
    validation, _, actual = collect(config, output, "validation", config["validation_particle_seeds"])
    validation = rescale(validation, fit["noise_scale"])
    csv_rows(output / "validation.csv", validation)
    calibration = b6.summarize(primary, training, validation, references, config)
    write_json(output / "calibration_summary.json", calibration)
    try:
        uncertainty = validation_seed_diagnostics(training, validation, config, output)
    except ValueError as error:
        uncertainty = dict(status="unavailable", reason=str(error))
    write_json(output / "seed_uncertainty.json", uncertainty)
    summary = dict(stage="P2-B7", status="NOISE-PASS" if calibration["status"] == "passed" else "CALIBRATION-STOP",
                   reference_arm=config["reference_arm"], noise_scale=fit["noise_scale"], calibration_gates=calibration["gates"],
                   new_tracking_attempts=attempts+actual, training_seeds=32, validation_seeds=32, reused_primary_frames=len(primary),
                   validation_started=True, optimizer_runs=0, ioc_access=False, gp_or_acquisition_changes=False,
                   seed_uncertainty="descriptive only; unchanged empirical acceptance gates",
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
    if config["predecessor_config_sha256"] != sha256(FROZEN / "p2_b6/config.json"):
        raise ValueError("B6 predecessor configuration mismatch")
    for stage in PREVIOUS_STAGES:
        folder = FROZEN / stage
        index = json.loads((folder / "result_index.json").read_text())
        if any(sha256(folder / name) != digest for name, digest in index.get("artifacts_sha256", {}).items()):
            raise ValueError(f"predecessor artifact mismatch: {stage}")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.config, args.output_dir / "config.json")
    sources = list(json.loads((FROZEN / "p2_b6/metadata.json").read_text())["source_sha256"])
    sources.append("scripts/calibrate_emit_infobax_expanded_noise.py")
    hashes = {name: sha256(REPO_ROOT / name) for name in sources}
    prior = {str(p.relative_to(REPO_ROOT)): sha256(p) for stage in PREVIOUS_STAGES
             for p in sorted((FROZEN / stage).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    write_json(args.output_dir / "metadata.json", dict(
        config_sha256=sha256(args.config), source_sha256=hashes, predecessor_evidence_sha256=prior,
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
        elegant_binary_sha256=sha256(shutil.which("elegant")), excluded_prior_particle_seeds=sorted(prior_particle_seeds()),
        worker_processes=config["workers"], model_workspaces="private TemporaryDirectory for each independent case and phase"))
    run(config, args.output_dir)
    if any(sha256(REPO_ROOT / name) != digest for name, digest in {**hashes, **prior}.items()):
        raise RuntimeError("frozen source or predecessor evidence changed")


if __name__ == "__main__":
    main()
