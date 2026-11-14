#!/usr/bin/env python3
"""P2-B10: fixed-history M0 versus transport-moment posterior comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import numpy as np
import scipy
from scipy.special import ndtr
from scripts.calibrate_emit_infobax_weighted_rms import read_rows
from scripts.diagnose_emit_infobax_measurement import csv_rows, sha256, write_json
from scripts.diagnose_emit_infobax_posterior import physicality, span_energy_fraction
from half_linac.src.optimization.infobax import FinitePoolSpec, InfoBAXConfig, PointObservation
from half_linac.src.optimization.infobax.algorithm import BeamEmittanceAlgorithm
from half_linac.src.optimization.infobax.core import stable_seed
from half_linac.src.optimization.infobax.posterior import FinitePoolM0
from half_linac.src.optimization.infobax.transport_posterior import TransportMomentGP

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_transport_moment_comparison.json"
FROZEN = REPO_ROOT / "src/optimization/infobax/benchmarks"
PREVIOUS_STAGES = ("p2_b2", "p2_b3", "p2_b4", "p2_b4_1", "p2_b5", "p2_b6", "p2_b7", "p2_b8", "p2_b9")


def prediction_metrics(posterior, observed_indices, reference, reference_noise):
    missing = np.ones(posterior.query_count, dtype=bool)
    missing[list(observed_indices)] = False
    if not missing.any():
        raise ValueError("withheld prediction metrics require unobserved points")
    residual = np.asarray(reference)-posterior.mean
    variance = np.diag(posterior.covariance)+np.asarray(reference_noise)**2
    if np.any(variance <= 0) or not np.all(np.isfinite(variance)):
        raise ValueError("predictive variance must be finite and positive")
    z = residual/np.sqrt(variance)
    rmse = float(np.sqrt(np.mean(residual[missing]**2)))
    normalizer = float(np.sqrt(np.mean(np.asarray(reference)**2)))
    return dict(withheld_queries=int(missing.sum()), withheld_rmse_mm2=rmse,
                withheld_normalized_rmse=rmse/normalizer,
                withheld_standardized_mean_square=float(np.mean(z[missing]**2)),
                withheld_95_coverage=float(np.mean(np.abs(z[missing]) <= 1.959963984540054)),
                withheld_nlpd=float(np.mean(0.5*np.log(2*np.pi*variance[missing])+0.5*z[missing]**2)))


def paired_seed_summary(rows, config):
    metrics = ("invalid_output_probability", "recommendation_available", "withheld_normalized_rmse", "withheld_nlpd")
    indexed = {(r["case"], r["seed"], r["history_policy"], r["observations"], r["model"]): r for r in rows}
    result = {}
    for count in config["observation_counts"]:
        by_seed = {key: [] for key in metrics}
        seeds = sorted({r["seed"] for r in rows})
        cases = sorted({r["case"] for r in rows})
        policies = sorted({r["history_policy"] for r in rows})
        for seed in seeds:
            differences = {key: [] for key in metrics}
            for case in cases:
                for policy in policies:
                    pair = [indexed[(case, seed, policy, count, model)] for model in config["models"]]
                    for key in metrics:
                        differences[key].append(float(pair[1][key])-float(pair[0][key]))
            for key in metrics:
                by_seed[key].append(float(np.mean(differences[key])))
        rng = np.random.default_rng(config["bootstrap"]["seed"]+count)
        draws = rng.integers(len(seeds), size=(config["bootstrap"]["samples"], len(seeds)))
        result[str(count)] = {}
        for key, differences in by_seed.items():
            values = np.asarray(differences)
            result[str(count)][key] = dict(mean_transport_minus_m0=float(values.mean()),
                paired_seed_95_interval=np.quantile(np.mean(values[draws], axis=1), [0.025, 0.975]).tolist(),
                seed_count=len(seeds), seed_differences=values.tolist())
    return result


def evaluate_posterior(posterior, pool, observations, reference, reference_noise, tag, config, sample_seed):
    samples = posterior.sample(config["samples"], sample_seed)
    physics = physicality(samples, pool)
    candidates = [dict(**tag, **row) for row in physics.pop("candidates")]
    algorithm = BeamEmittanceAlgorithm(pool)
    recommendation = algorithm.summarize(samples, recommendation_invalid_threshold=config["recommendation_invalid_threshold"])
    curves = samples.reshape(-1, pool.candidate_count, pool.scan_count)
    residual = np.eye(pool.scan_count)-pool.design_matrix @ np.linalg.pinv(pool.design_matrix)
    sample_residual = float(np.linalg.norm(curves @ residual.T)/max(np.linalg.norm(curves), 1e-30))
    off_span = []
    for j in range(pool.candidate_count):
        block = slice(j*pool.scan_count, (j+1)*pool.scan_count)
        off_span.append(span_energy_fraction(posterior.mean[block], posterior.covariance[block, block], pool.design_matrix))
    observed = {row.query_index for row in observations}
    query_rows = []
    std = np.sqrt(np.maximum(np.diag(posterior.covariance), 0))
    negative = np.zeros(pool.query_count)
    positive_std = std > 0
    negative[positive_std] = ndtr(-posterior.mean[positive_std]/std[positive_std])
    negative[~positive_std] = posterior.mean[~positive_std] <= 0
    for index in range(pool.query_count):
        query_rows.append(dict(**tag, query_index=index, observed=index in observed,
            posterior_mean_mm2=float(posterior.mean[index]), posterior_std_mm2=float(std[index]),
            negative_variance_probability=float(negative[index]), finite_primary_variance_mm2=float(reference[index])))
    row = dict(**tag, **physics, **prediction_metrics(posterior, observed, reference, reference_noise),
        recommendation_available=recommendation.recommended_candidate is not None,
        recommended_candidate_index=recommendation.recommended_candidate,
        expected_off_span_energy_mean=float(np.mean(off_span)), maximum_off_span_energy=max(off_span),
        sample_relative_transport_residual=sample_residual,
        unobserved_negative_variance_mass_median=float(np.median(negative[[i for i in range(pool.query_count) if i not in observed]])),
        latent_dimension=getattr(posterior, "latent_dimension", pool.query_count), monte_carlo_seed=sample_seed)
    return row, candidates, query_rows


def validate_config(config):
    if (config["schema"] != "half_infobax_p2_b10_transport_moment_v1" or config["stage"] != "P2-B10"
            or config["models"] != ["m0", "transport_moments"] or config["observation_counts"] != [6, 14]
            or config["samples"] != 16384 or config["recommendation_invalid_threshold"] != 0.5
            or config["transport_relative_tolerance"] != 1e-10
            or config["bootstrap"] != dict(samples=5000, seed=2026111310, confidence=0.95,
                unit="resample 16 complete policy seeds, keeping both cases and both historical policies; conditional on frozen histories")):
        raise ValueError("invalid fixed comparison or changed sampling/gate settings")


def run(config, output):
    pools = json.loads((FROZEN / "p2_b9/pools.json").read_text())
    histories = json.loads((FROZEN / "p2_b8/trajectories.json").read_text())
    primary = read_rows(FROZEN / "p2_b8/reference_primary.csv")
    old_summary = read_rows(FROZEN / "p2_b9/posteriors.csv")
    benchmark = json.loads((FROZEN / "p2_b8/config.json").read_text())
    rows, candidate_rows, predictions, controls = [], [], [], []
    for case in benchmark["cases"]:
        pool = FinitePoolSpec(**pools[case["id"]]["spec"])
        assert pool.fingerprint == pools[case["id"]]["fingerprint"]
        truth_rows = [next(r for r in primary if r["case"] == case["id"] and r["workpoint"] == work and r["scan_value"] == scan)
                      for work in benchmark["workpoints"] for scan in benchmark["scan_values"]]
        reference = np.array([r["corrected_variance_mm2"] for r in truth_rows])
        reference_noise = np.array([r["noise_std_mm2"] for r in truth_rows])
        for history in [r for r in histories if r["case"] == case["id"]]:
            assert history["checkpoint"]["pool_fingerprint"] == pool.fingerprint
            core_config = InfoBAXConfig.from_dict(history["checkpoint"]["config"])
            for count in config["observation_counts"]:
                observations = [PointObservation(**r) for r in history["checkpoint"]["observations"][:count]]
                seed = (stable_seed("summary", history["seed"], "M0", 0) if count == 6 else
                        stable_seed("p2_b9_physicality", case["id"], history["seed"], history["policy"]))
                for model in config["models"]:
                    fitter = FinitePoolM0(pool, core_config) if model == "m0" else TransportMomentGP(pool, core_config)
                    posterior = fitter.fit(observations)
                    tag = dict(case=case["id"], seed=history["seed"], history_policy=history["policy"], observations=count, model=model)
                    row, cs, qs = evaluate_posterior(posterior, pool, observations, reference, reference_noise, tag, config, seed)
                    rows.append(row); candidate_rows.extend(cs); predictions.extend(qs)
                    if model == "m0" and count == 14:
                        expected = next(r for r in old_summary if r["case"] == case["id"] and r["seed"] == history["seed"]
                                        and r["policy"] == history["policy"] and r["sample_kind"] == "enhanced")
                        fields = ("invalid_output_probability", "all_candidates_physical_probability",
                                  "no_positive_curve_candidate_probability", "positive_curves_without_physical_covariance_probability")
                        controls.append(dict(**tag, exact_match=all(row[k] == expected[k] for k in fields)))
            print(f"B10 case={case['id']} seed={history['seed']} history={history['policy']} compared", flush=True)
        for name, data in (("posteriors.csv", rows), ("physicality.csv", candidate_rows), ("predictions.csv", predictions)):
            csv_rows(output / name, data)
        write_json(output / "m0_controls.json", controls)
    paired = paired_seed_summary(rows, config)
    write_json(output / "paired_summary.json", paired)
    grouped = {}
    for count in config["observation_counts"]:
        grouped[str(count)] = {}
        for model in config["models"]:
            selected = [r for r in rows if r["observations"] == count and r["model"] == model]
            grouped[str(count)][model] = dict(histories=len(selected),
                available_recommendations=sum(r["recommendation_available"] for r in selected),
                mean_invalid_probability=float(np.mean([r["invalid_output_probability"] for r in selected])),
                mean_withheld_normalized_rmse=float(np.mean([r["withheld_normalized_rmse"] for r in selected])),
                mean_withheld_nlpd=float(np.mean([r["withheld_nlpd"] for r in selected])),
                mean_withheld_95_coverage=float(np.mean([r["withheld_95_coverage"] for r in selected])),
                mean_withheld_standardized_mean_square=float(np.mean([r["withheld_standardized_mean_square"] for r in selected])),
                maximum_transport_residual=max(r["sample_relative_transport_residual"] for r in selected),
                mean_unobserved_negative_variance_mass_median=float(np.mean([r["unobserved_negative_variance_mass_median"] for r in selected])))
    moment_rows = [r for r in rows if r["model"] == "transport_moments"]
    valid = (len(rows) == 256 and len(controls) == 64 and all(r["exact_match"] for r in controls)
             and max(r["sample_relative_transport_residual"] for r in moment_rows) <= config["transport_relative_tolerance"])
    summary = dict(stage="P2-B10", status="COMPARISON-COMPLETE" if valid else "COMPARISON-AUDIT-STOP",
        posterior_comparisons=128, posterior_fits=len(rows), exact_final_m0_controls=sum(r["exact_match"] for r in controls),
        by_observation_count=grouped, paired_differences=paired,
        new_particle_measurements=0, optimizer_runs=0, ioc_access=False, production_model_changed=False,
        interpretation="Counterfactual posteriors on frozen M0/Random histories, not a new adaptive-policy benchmark",
        b8_status="BENCHMARK-STOP preserved", real_machine_acceptance="not established")
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_bytes())
    validate_config(config)
    if config["predecessor_config_sha256"] != sha256(FROZEN / "p2_b9/config.json"):
        raise ValueError("B9 predecessor configuration mismatch")
    for stage in PREVIOUS_STAGES:
        folder = FROZEN / stage
        index = json.loads((folder / "result_index.json").read_text())
        if any(sha256(folder / name) != digest for name, digest in index.get("artifacts_sha256", {}).items()):
            raise ValueError(f"frozen predecessor artifact changed: {stage}")
    previous_sources = json.loads((FROZEN / "p2_b8/metadata.json").read_text())["source_sha256"]
    for name, digest in previous_sources.items():
        if name.startswith("src/optimization/infobax/") and sha256(REPO_ROOT / name) != digest:
            raise ValueError(f"M0 implementation changed: {name}")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.config, args.output_dir / "config.json")
    sources = [Path(__file__), REPO_ROOT / "scripts/diagnose_emit_infobax_posterior.py",
               REPO_ROOT / "scripts/diagnose_emit_infobax_measurement.py", REPO_ROOT / "scripts/calibrate_emit_infobax_weighted_rms.py",
               *sorted((REPO_ROOT / "src/optimization/infobax").glob("*.py"))]
    hashes = {str(p.relative_to(REPO_ROOT)): sha256(p) for p in sources}
    prior = {str(p.relative_to(REPO_ROOT)): sha256(p) for stage in PREVIOUS_STAGES
             for p in sorted((FROZEN / stage).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    write_json(args.output_dir / "metadata.json", dict(config_sha256=sha256(args.config), source_sha256=hashes,
        predecessor_evidence_sha256=prior, python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        numeric_only=True, machine_reads_or_writes=0))
    run(config, args.output_dir)
    if any(sha256(REPO_ROOT / name) != digest for name, digest in {**hashes, **prior}.items()):
        raise RuntimeError("frozen source or predecessor evidence changed")


if __name__ == "__main__":
    main()
