#!/usr/bin/env python3
"""P2-B9: frozen M0 posterior replay, physicality and target audit."""

from __future__ import annotations

import argparse
from dataclasses import asdict
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
from scipy.special import ndtr
from scripts.calibrate_emit_infobax_weighted_rms import read_rows
from scripts.diagnose_emit_infobax_measurement import csv_rows, relative_error, sha256, write_json
from scripts.validate_emit_infobax_reference_precision import PrecisionBeam
from half_linac.src.apps.emit_measure.infobax_vm import FiniteWorkpointTable, MachineVariable, build_half_vm_infobax_pool
from half_linac.src.optimization.infobax import InfoBAXCore
from half_linac.src.optimization.infobax.algorithm import BeamEmittanceAlgorithm, beta_gamma
from half_linac.src.optimization.infobax.core import stable_seed
from half_linac.src.optimization.infobax.posterior import FinitePoolM0
from half_linac.src.shared.beam_matrix import reconstruct_beam_matrix
from half_linac.src.shared.machine_profile import load_app_context

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_posterior_diagnostic.json"
FROZEN = REPO_ROOT / "src/optimization/infobax/benchmarks"
PREVIOUS_STAGES = ("p2_b2", "p2_b3", "p2_b4", "p2_b4_1", "p2_b5", "p2_b6", "p2_b7", "p2_b8")
PARTITIONS = ("nonfinite_curve", "nonpositive_curve", "nonfinite_moments", "nonpositive_diagonal",
              "nonpositive_determinant", "physical")


def wilson_interval(successes, count):
    if count <= 0 or not 0 <= successes <= count:
        raise ValueError("invalid Monte Carlo count")
    z = 1.959963984540054
    p = successes/count
    center = (p+z*z/(2*count))/(1+z*z/count)
    half = z*np.sqrt(p*(1-p)/count+z*z/(4*count*count))/(1+z*z/count)
    return [float(max(0, center-half)), float(min(1, center+half))]


def physicality(samples, pool):
    curves = np.asarray(samples).reshape(-1, pool.candidate_count, pool.scan_count)
    moments = curves @ np.linalg.pinv(pool.design_matrix).T
    finite_curve = np.all(np.isfinite(curves), axis=2)
    positive_curve = finite_curve & np.all(curves > 0, axis=2)
    finite_moments = np.all(np.isfinite(moments), axis=2)
    positive_diagonal = (moments[..., 0] > 0) & (moments[..., 2] > 0)
    determinant = moments[..., 0]*moments[..., 2]-moments[..., 1]**2
    coefficient_physical = finite_moments & positive_diagonal & (determinant > 0)
    valid = positive_curve & coefficient_physical
    masks = (~finite_curve, finite_curve & ~positive_curve,
             positive_curve & ~finite_moments,
             positive_curve & finite_moments & ~positive_diagonal,
             positive_curve & finite_moments & positive_diagonal & ~(determinant > 0), valid)
    assert np.all(np.sum(masks, axis=0) == 1)
    production = BeamEmittanceAlgorithm(pool).evaluate(samples)
    assert np.array_equal(valid, production.candidate_physical)
    invalid = ~np.any(valid, axis=1)
    assert np.array_equal(invalid, production.categories == pool.candidate_count)
    no_positive_curve = ~np.any(positive_curve, axis=1)
    positive_without_covariance = np.any(positive_curve, axis=1) & invalid
    count = len(curves)
    candidates = []
    for j in range(pool.candidate_count):
        candidates.append(dict(candidate_index=j,
            **{name: int(mask[:, j].sum()) for name, mask in zip(PARTITIONS, masks)},
            curve_positive_probability=float(positive_curve[:, j].mean()),
            covariance_physical_probability=float(coefficient_physical[:, j].mean()),
            physical_probability=float(valid[:, j].mean())))
    all_count = int(np.all(valid, axis=1).sum())
    return dict(sample_count=count, invalid_output_probability=float(invalid.mean()),
                invalid_output_mc_interval=wilson_interval(int(invalid.sum()), count),
                no_positive_curve_candidate_probability=float(no_positive_curve.mean()),
                positive_curves_without_physical_covariance_probability=float(positive_without_covariance.mean()),
                all_candidates_physical_probability=all_count/count,
                zero_all_physical_one_sided_95_upper=(float(1-0.05**(1/count)) if all_count == 0 else None),
                candidates=candidates)


def span_energy_fraction(mean, covariance, design):
    residual = np.eye(len(mean))-design @ np.linalg.pinv(design)
    numerator = float(np.linalg.norm(residual @ mean)**2 + np.trace(residual @ covariance @ residual.T))
    denominator = float(mean @ mean + np.trace(covariance))
    if denominator <= 0:
        raise ValueError("curve energy must be positive")
    return max(0.0, numerator)/denominator


def pool_payload(pool):
    return dict(candidate_ids=list(pool.candidate_ids), candidate_coordinates=pool.candidate_coordinates.tolist(),
                scan_values=pool.scan_values.tolist(), scan_coordinates=pool.scan_coordinates.tolist(),
                design_matrix=pool.design_matrix.tolist(), energy_mev=pool.energy_mev,
                observation_noise_std_mm2=pool.observation_noise_std_mm2.tolist(),
                measurement_contract_fingerprint=pool.measurement_contract_fingerprint)


def rebuild_pool(root, config, case):
    scale = json.loads((FROZEN / "p2_b7/noise_scale_fit.json").read_text())["noise_scale"]
    beam = PrecisionBeam(root, config, case, config["particles"], scale)
    context = load_app_context("emit_measure", machine_id="half", control_backend="vm")
    table = FiniteWorkpointTable(tuple(f"workpoint-{i}" for i in range(len(config["workpoints"]))),
                                (MachineVariable("QT01"),), np.array(config["workpoints"])[:, None])
    class CachedMaps:
        def get_map(self, _first, _second, **kwargs):
            matrix = np.eye(4)
            matrix[0, :2] = beam.projections[float(kwargs["k1"])]
            return matrix
    pool = build_half_vm_infobax_pool(context, table, scan_quad="QT02", screen="PRF07", plane="x",
        scan_values=config["scan_values"], energy_mev=config["energy_mev"],
        observation_noise_std_mm2=config["forecast_noise_std_mm2"], model_backend=CachedMaps(),
        measurement_method=config["measurement_method"], rms_config=beam.rms_config)
    assert beam.attempts == 0
    return pool, [beam.projections[float(k)] for k in config["scan_values"]]


def reference_audit(pool, projections, primary, references, config, case_id):
    curves, errors, moments, momentum, truth = [], [], [], [], []
    for work in config["workpoints"]:
        rows = [next(r for r in primary if r["case"] == case_id and r["workpoint"] == work and r["scan_value"] == scan)
                for scan in config["scan_values"]]
        curves.append([r["corrected_variance_mm2"] for r in rows])
        errors.append([r["noise_std_mm2"] for r in rows])
        momentum.append(float(np.mean([r["entrance_beta_gamma"] for r in rows])))
        truth.append(float(np.mean([r["entrance_emittance_mm_mrad"] for r in rows])))
        moments.append(np.mean([r["entrance_moments"] for r in rows], axis=0))
    evaluated = BeamEmittanceAlgorithm(pool).evaluate(np.asarray(curves).reshape(1, -1))
    output = []
    for j, work in enumerate(config["workpoints"]):
        curve, noise = np.array(curves[j]), np.array(errors[j])
        size = np.sqrt(curve)*1e-3
        uw = reconstruct_beam_matrix(projections, size)
        weighted = reconstruct_beam_matrix(projections, size, noise/(2*np.sqrt(curve))*1e-3)
        frozen = next(r for r in references if r["case"] == case_id and r["workpoint"] == work)
        value = float(evaluated.emittances_mm_mrad[0, j]) if evaluated.candidate_physical[0, j] else None
        uw_fixed = uw.geometric_emittance_m_rad*beta_gamma(pool.energy_mev)*1e6 if uw.valid else None
        w_fixed = weighted.geometric_emittance_m_rad*beta_gamma(pool.energy_mev)*1e6 if weighted.valid else None
        w_entrance = weighted.geometric_emittance_m_rad*momentum[j]*1e6 if weighted.valid else None
        output.append(dict(case=case_id, workpoint=work, candidate_index=j, m0_physical=bool(evaluated.candidate_physical[0, j]),
            m0_emittance_mm_mrad=value, shared_unweighted_status=uw.status, weighted_status=weighted.status,
            shared_unweighted_fixed_energy_mm_mrad=uw_fixed, weighted_fixed_energy_mm_mrad=w_fixed,
            weighted_entrance_momentum_mm_mrad=w_entrance, frozen_weighted_reference_mm_mrad=frozen["emittance_mm_mrad"],
            entrance_reference_mm_mrad=truth[j], entrance_moments_si=moments[j].tolist(),
            m0_shared_uw_relative_error=relative_error(value, uw_fixed),
            weighted_reference_reproduction_error=relative_error(w_entrance, frozen["emittance_mm_mrad"]),
            unweighted_vs_weighted_relative_difference=relative_error(value, w_fixed),
            reference_curve_off_span_energy=span_energy_fraction(curve, np.zeros((len(curve), len(curve))), pool.design_matrix)))
    return output


def diagnose_posterior(core, pool, primary_curve, primary_noise, tag, count, sample_seed):
    posterior = FinitePoolM0(pool, core.config).fit(core.observations)
    samples = posterior.sample(count, sample_seed)
    physics = physicality(samples, pool)
    evaluated_mean = BeamEmittanceAlgorithm(pool).evaluate(posterior.mean[None, :])
    observed = {r.query_index: r for r in core.observations}
    queries, candidates = [], []
    for index in range(pool.query_count):
        deviation = float(np.sqrt(posterior.covariance[index, index]))
        queries.append(dict(**tag, query_index=index, observed=index in observed,
            posterior_mean_mm2=float(posterior.mean[index]), latent_std_mm2=deviation,
            negative_variance_probability=float(ndtr(-posterior.mean[index]/deviation)),
            finite_primary_variance_mm2=float(primary_curve[index]),
            finite_primary_prediction_z=float((primary_curve[index]-posterior.mean[index])/
                                             np.sqrt(deviation**2+primary_noise[index]**2))))
    for j, entry in enumerate(physics.pop("candidates")):
        interval = slice(j*pool.scan_count, (j+1)*pool.scan_count)
        candidates.append(dict(**tag, **entry, candidate_id=pool.candidate_ids[j],
            candidate_coordinate=float(pool.candidate_coordinates[j, 0]),
            off_span_expected_energy=span_energy_fraction(posterior.mean[interval], posterior.covariance[interval, interval], pool.design_matrix),
            posterior_mean_is_physical=bool(evaluated_mean.candidate_physical[0, j])))
    target = np.array([r.variance_mm2 for r in core.observations])
    components = [float(target.std()), float(0.2*abs(np.median(target))), float(np.median(pool.observation_noise_std_mm2))]
    output = dict(**tag, **physics, posterior_mean_physical_candidates=int(evaluated_mean.candidate_physical.sum()),
        target_spread_mm2=components[0], median_scale_floor_mm2=components[1], forecast_scale_floor_mm2=components[2],
        scale_dominant_component=("observed_spread", "median_floor", "forecast_floor")[int(np.argmax(components))],
        observations_with_likelihood_jitter_above_noise=sum(1e-10 > r.noise_std_mm2**2 for r in core.observations))
    return output, candidates, queries


def validate_config(config):
    if (config["schema"] != "half_infobax_p2_b9_posterior_diagnostic_v1" or config["stage"] != "P2-B9"
            or config["endpoints"] != [6, 14] or config["enhanced_samples"] != 16384
            or config["enhanced_seed_namespace"] != "p2_b9_physicality"
            or config["failed_recommendation_progression"] != list(range(6, 15))
            or config["reference_numerical_tolerance"] != 1e-8):
        raise ValueError("invalid bounded diagnostic settings")


def run(config, output):
    source = FROZEN / "p2_b8"
    benchmark = json.loads((source / "config.json").read_text())
    trajectories = json.loads((source / "trajectories.json").read_text())
    primary, references = read_rows(source / "reference_primary.csv"), read_rows(source / "references.csv")
    posteriors, candidates, queries, replay, target_audit, pools = [], [], [], [], [], {}
    for case in benchmark["cases"]:
        with TemporaryDirectory(prefix="infobax-b9-maps-") as temporary:
            pool, projections = rebuild_pool(Path(temporary), benchmark, case)
        pools[case["id"]] = dict(fingerprint=pool.fingerprint, spec=pool_payload(pool), projections=projections)
        target_audit.extend(reference_audit(pool, projections, primary, references, benchmark, case["id"]))
        ordered = [next(r for r in primary if r["case"] == case["id"] and r["workpoint"] == work and r["scan_value"] == scan)
                   for work in benchmark["workpoints"] for scan in benchmark["scan_values"]]
        curve = np.array([r["corrected_variance_mm2"] for r in ordered])
        noise = np.array([r["noise_std_mm2"] for r in ordered])
        for row in [r for r in trajectories if r["case"] == case["id"]]:
            assert row["checkpoint"]["pool_fingerprint"] == pool.fingerprint
            counts = config["failed_recommendation_progression"] if row["relative_regret"] is None else config["endpoints"]
            for n in counts:
                checkpoint = dict(row["checkpoint"], observations=row["checkpoint"]["observations"][:n])
                core = InfoBAXCore.from_checkpoint(pool, checkpoint)
                tag = dict(case=row["case"], seed=row["seed"], policy=row["policy"], observations=n, sample_kind="native")
                sample_seed = stable_seed("summary", row["seed"], "M0", n-core.config.initial_actions)
                diag, cs, qs = diagnose_posterior(core, pool, curve, noise, tag, core.config.summary_samples, sample_seed)
                posteriors.append(diag); candidates.extend(cs); queries.extend(qs)
                if n in config["endpoints"]:
                    expected = row["initial_summary"] if n == 6 else row["recommendation"]
                    actual = json.loads(json.dumps(asdict(core.recommend())))
                    replay.append(dict(**tag, pool_fingerprint=pool.fingerprint, exact_match=actual == expected,
                                       actual=actual, expected=expected))
                if n == 14:
                    extra_seed = stable_seed(config["enhanced_seed_namespace"], row["case"], row["seed"], row["policy"])
                    diag, cs, _ = diagnose_posterior(core, pool, curve, noise, dict(tag, sample_kind="enhanced"), config["enhanced_samples"], extra_seed)
                    posteriors.append(diag); candidates.extend(cs)
            print(f"B9 case={row['case']} seed={row['seed']} policy={row['policy']} replayed", flush=True)
        for name, data in (("posteriors.csv", posteriors), ("candidates.csv", candidates), ("queries.csv", queries), ("reference_audit.csv", target_audit)):
            csv_rows(output / name, data)
        write_json(output / "pools.json", pools)
        write_json(output / "replay.json", replay)
    final = [r for r in posteriors if r["observations"] == 14 and r["sample_kind"] == "native"]
    enhanced = [r for r in posteriors if r["sample_kind"] == "enhanced"]
    failures = [dict(case=r["case"], seed=r["seed"], policy=r["policy"]) for r in trajectories if r["relative_regret"] is None]
    failure_keys = {(r["case"], r["seed"], r["policy"]) for r in failures}
    enhanced_failures = [r for r in enhanced if (r["case"], r["seed"], r["policy"]) in failure_keys]
    audit_ok = (len(replay) == 128 and all(r["exact_match"] for r in replay)
                and all(r["m0_shared_uw_relative_error"] is not None and r["m0_shared_uw_relative_error"] <= config["reference_numerical_tolerance"]
                        and r["weighted_reference_reproduction_error"] is not None and r["weighted_reference_reproduction_error"] <= config["reference_numerical_tolerance"] for r in target_audit))
    summary = dict(stage="P2-B9", status="DIAGNOSTIC-COMPLETE" if audit_ok else "REPLAY-STOP",
        endpoint_replays=len(replay), exact_replays=sum(r["exact_match"] for r in replay),
        native_posterior_snapshots=sum(r["sample_kind"] == "native" for r in posteriors), enhanced_posteriors=len(enhanced),
        enhanced_failure_details=enhanced_failures, native_final_invalid_probability_range=[min(r["invalid_output_probability"] for r in final), max(r["invalid_output_probability"] for r in final)],
        native_final_no_positive_curve_candidate_range=[min(r["no_positive_curve_candidate_probability"] for r in final), max(r["no_positive_curve_candidate_probability"] for r in final)],
        native_final_positive_curves_without_covariance_range=[min(r["positive_curves_without_physical_covariance_probability"] for r in final), max(r["positive_curves_without_physical_covariance_probability"] for r in final)],
        enhanced_zero_all_physical_runs=sum(r["all_candidates_physical_probability"] == 0 for r in enhanced),
        max_weighted_reference_reproduction_error=max(r["weighted_reference_reproduction_error"] for r in target_audit),
        max_m0_shared_unweighted_error=max(r["m0_shared_uw_relative_error"] for r in target_audit),
        max_unweighted_weighted_difference=max(r["unweighted_vs_weighted_relative_difference"] for r in target_audit),
        final_forecast_floor_dominant_runs=sum(r["scale_dominant_component"] == "forecast_floor" for r in final),
        transport_model_map_calculations=18, particle_tracking_attempts=0, optimizer_runs=0, ioc_access=False,
        gp_acquisition_or_threshold_changes=False, b8_outcome="BENCHMARK-STOP preserved",
        monte_carlo_interpretation="Intervals describe draws from fitted posteriors only; no policy performance or population acceptance claim")
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_bytes())
    validate_config(config)
    if config["predecessor_config_sha256"] != sha256(FROZEN / "p2_b8/config.json"):
        raise ValueError("B8 predecessor mismatch")
    for stage in PREVIOUS_STAGES:
        folder = FROZEN / stage
        index = json.loads((folder / "result_index.json").read_text())
        if any(sha256(folder / p) != h for p, h in index.get("artifacts_sha256", {}).items()):
            raise ValueError(f"frozen predecessor changed: {stage}")
    b8meta = json.loads((FROZEN / "p2_b8/metadata.json").read_text())
    for name, digest in b8meta["source_sha256"].items():
        if name.startswith("src/optimization/infobax/") and sha256(REPO_ROOT / name) != digest:
            raise ValueError(f"M0 source differs from B8: {name}")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.config, args.output_dir / "config.json")
    paths = [Path(__file__), REPO_ROOT / "scripts/check_emit_infobax_vm.py", REPO_ROOT / "scripts/diagnose_emit_infobax_measurement.py",
             REPO_ROOT / "scripts/validate_emit_infobax_reference_precision.py", REPO_ROOT / "scripts/calibrate_emit_infobax_weighted_rms.py",
             REPO_ROOT / "src/shared/beam_matrix.py", REPO_ROOT / "src/shared/elegant_backend/parser.py",
             REPO_ROOT / "src/shared/machine_profile/model_backend.py", *sorted((REPO_ROOT / "src/optimization/infobax").glob("*.py")),
             *sorted((REPO_ROOT / "src/apps/emit_measure").glob("infobax*.py"))]
    hashes = {str(p.relative_to(REPO_ROOT)): sha256(p) for p in paths}
    prior = {str(p.relative_to(REPO_ROOT)): sha256(p) for stage in PREVIOUS_STAGES
             for p in sorted((FROZEN / stage).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    write_json(args.output_dir / "metadata.json", dict(config_sha256=sha256(args.config), source_sha256=hashes,
        predecessor_evidence_sha256=prior, code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__, elegant_binary_sha256=sha256(shutil.which("elegant"))))
    run(config, args.output_dir)
    if any(sha256(REPO_ROOT / p) != h for p, h in {**hashes, **prior}.items()):
        raise RuntimeError("frozen source or prior evidence changed")


if __name__ == "__main__":
    main()
