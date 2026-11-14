#!/usr/bin/env python3
"""Frozen matched-budget M0 InfoBAX/Random comparison on an elegant finite pool."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import numpy as np
import scipy
from half_linac.src.optimization.infobax import FinitePoolSpec, InfoBAXConfig, InfoBAXCore, PointObservation
from half_linac.src.optimization.infobax.algorithm import beta_gamma
from half_linac.src.optimization.infobax.core import stable_seed
from half_linac.src.shared.beam_matrix import reconstruct_beam_matrix

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_m0_benchmark.json"


class FinitePoolEvaluator:
    """Evaluator-only complete scans; policies receive individual reveals."""

    def __init__(self, pool, variances, reference_emittances, *, case_id, seed):
        self.pool = pool
        truth = np.asarray(variances, dtype=float).reshape(pool.query_count)
        self._reference = np.asarray(reference_emittances, dtype=float)
        noise = np.random.default_rng(stable_seed("measurement", case_id, seed)).normal(
            size=pool.query_count
        ) * pool.observation_noise_std_mm2
        self._measurements = truth + noise
        self._revealed = set()

    def reveal(self, index):
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError("evaluator query must be an integer")
        self.pool.query_parts(index)
        if index in self._revealed:
            raise ValueError("duplicate evaluator query")
        self._revealed.add(index)
        # PointObservation refuses nonpositive values. No clipping or retries.
        return PointObservation(index, float(self._measurements[index]),
                                float(self.pool.observation_noise_std_mm2[index]))

    def evaluate(self, recommendation):
        index = recommendation.recommended_candidate_index
        if index is None:
            return {"relative_regret": None, "best_hit": False}
        best = float(np.min(self._reference))
        return {"relative_regret": float(self._reference[index] / best - 1),
                "best_hit": bool(index == int(np.argmin(self._reference)))}


def run_policy(pool, core_config, total, policy, evaluator, *, case_id):
    core = InfoBAXCore(pool, core_config)
    rng = np.random.default_rng(stable_seed("random", case_id, core_config.seed))
    traces = []
    attempts = []
    initial_summary = None
    status, error = "completed", None
    started = time.monotonic()
    try:
        for step in range(total):
            tick = time.monotonic()
            if step < core_config.initial_actions or policy == "infobax":
                query = core.propose()
                if query.status != "ready":
                    status, error = query.status, "automatic execution stopped by core reliability gate"
                    break
                index = query.query_index
                raw_eig = query.raw_eig_nats
            elif policy == "random":
                index = int(rng.choice(core.available_indices))
                raw_eig = None
            else:
                raise ValueError("unsupported policy")
            selection_seconds = time.monotonic() - tick
            attempts.append(index)
            observation = evaluator.reveal(index)
            core.observe(observation)
            traces.append(dict(step=step, **observation.to_dict(), raw_eig_nats=raw_eig,
                               selection_seconds=selection_seconds))
            if len(core.observations) == core_config.initial_actions:
                initial_summary = asdict(core.recommend())
        recommendation = core.recommend() if initial_summary is not None else None
    except Exception as failure:
        status, error = "failed", f"{type(failure).__name__}: {failure}"
        recommendation = None
    evaluated = evaluator.evaluate(recommendation) if recommendation is not None else {
        "relative_regret": None, "best_hit": False,
    }
    return dict(case=case_id, seed=core_config.seed, policy=policy, status=status, error=error,
                observations=len(core.observations), queries=traces, initial_summary=initial_summary,
                attempted_query_indices=attempts,
                recommendation=asdict(recommendation) if recommendation is not None else None,
                checkpoint=core.to_checkpoint(), elapsed_seconds=time.monotonic() - started, **evaluated)


def paired_summary(rows, config):
    by_key = {(row["case"], row["seed"], row["policy"]): row for row in rows}
    differences = []
    for seed in config["seeds"]:
        seed_differences = []
        for case in config["cases"]:
            pair = [by_key.get((case["id"], seed, policy)) for policy in ("infobax", "random")]
            if all(row is not None and row["relative_regret"] is not None for row in pair):
                seed_differences.append(pair[0]["relative_regret"] - pair[1]["relative_regret"])
        # Resample independent paired trajectories, keeping both cases of each
        # seed together; never bootstrap individual K1 observations.
        if len(seed_differences) == len(config["cases"]):
            differences.append(float(np.mean(seed_differences)))
    interval = None
    if differences:
        rng = np.random.default_rng(config["bootstrap_seed"])
        boot = np.mean(rng.choice(differences, size=(config["bootstrap_samples"], len(differences))), axis=1)
        interval = [float(value) for value in np.quantile(boot, (0.025, 0.975))]
    policies = {}
    expected_runs = len(config["seeds"]) * len(config["cases"])
    for policy in config["policies"]:
        selected = [row for row in rows if row["policy"] == policy]
        valid = [row for row in selected if row["relative_regret"] is not None]
        policies[policy] = dict(
            expected_runs=expected_runs, completed_runs=sum(row["status"] == "completed" for row in selected),
            valid_recommendations=len(valid), recommendation_rate=len(valid) / expected_runs,
            mean_relative_regret_conditional=float(np.mean([row["relative_regret"] for row in valid])) if valid else None,
            best_hit_rate_unconditional=sum(row["best_hit"] for row in selected) / expected_runs,
            mean_all_candidates_physical_probability=float(np.mean([
                row["recommendation"]["all_candidates_physical_probability"]
                for row in selected if row["recommendation"] is not None
            ])) if any(row["recommendation"] is not None for row in selected) else None,
            mean_entropy_change=float(np.mean([
                row["recommendation"]["entropy_nats"] - row["initial_summary"]["entropy_nats"]
                for row in selected if row["recommendation"] is not None and row["initial_summary"] is not None
            ])) if any(row["recommendation"] is not None for row in selected) else None,
            median_active_selection_seconds=float(np.median([
                query["selection_seconds"] for row in selected
                for query in row["queries"][config["initial_observations"]:]
            ])) if any(len(row["queries"]) > config["initial_observations"] for row in selected) else None,
        )
    budget_ok = len(rows) == expected_runs * len(config["policies"]) and all(
        row["status"] == "completed" and row["observations"] == config["total_observations"]
        and len({query["query_index"] for query in row["queries"]}) == config["total_observations"]
        for row in rows
    )
    gates = dict(
        matched_budget_and_no_duplicates=budget_ok,
        recommendation_availability=all(
            item["recommendation_rate"] >= config["gates"]["minimum_recommendation_rate_per_policy"]
            for item in policies.values()
        ),
        regret_superiority=(interval is not None and len(differences) == len(config["seeds"])
                            and interval[1] < config["gates"]["paired_regret_difference_upper_95_ci_strictly_below"]),
    )
    return dict(policies=policies, complete_paired_seeds=len(differences),
                paired_mean_relative_regret_difference=float(np.mean(differences)) if differences else None,
                paired_difference_95_ci=interval, gates=gates)


def generate_case(root, config, case):
    from scripts.check_emit_infobax_vm import OfflineVm, prepare_workspace
    from half_linac.src.apps.emit_measure.infobax_vm import (
        FiniteWorkpointTable, HalfVmInfoBAXMachine, MachineVariable, build_half_vm_infobax_pool,
    )
    from half_linac.src.shared.machine_profile import build_model_backend

    context = prepare_workspace(root, sextupole_k2=case["sextupole_k2"], particles=config["particles"])
    table = FiniteWorkpointTable(tuple(f"workpoint-{index}" for index in range(len(config["workpoints"]))),
                                (MachineVariable("QT01"),), np.asarray(config["workpoints"])[:, None])
    backend = build_model_backend(context, energy_mev=config["energy_mev"], line_name="SMOKE")
    projections = []

    class RecordMaps:
        def get_map(self, *args, **kwargs):
            matrix = backend.get_map(*args, **kwargs)
            projections.append((float(matrix[0, 0]), float(matrix[0, 1])))
            return matrix

    pool = build_half_vm_infobax_pool(
        context, table, scan_quad="QT02", screen="PRF07", plane="x",
        scan_values=config["scan_values"], energy_mev=config["energy_mev"],
        observation_noise_std_mm2=config["observation_noise_std_mm2"], model_backend=RecordMaps(),
    )
    vm = OfflineVm(context, root)
    machine = HalfVmInfoBAXMachine(context, table, scan_quad="QT02", screen="PRF07", plane="x",
                                  runtime_json=vm.runtime_json, get=vm.get, put=vm.put, sleeper=vm.sleep)
    machine.validate_pool(pool)
    snapshot = machine.snapshot()
    variances, measurements = [], []
    try:
        for index in range(pool.query_count):
            candidate, scan = pool.query_parts(index)
            try:
                machine.apply_query(candidate, float(pool.scan_values[scan]))
                measurement = machine.measure_point(index, config["observation_noise_std_mm2"])
                variances.append(measurement.variance_mm2)
                measurements.append(dict(query_index=index, state="observed", **asdict(measurement)))
            except Exception as error:
                variances.append(float("nan"))
                measurements.append(dict(query_index=index, state="failed",
                                         error=f"{type(error).__name__}: {error}"))
    finally:
        machine.restore(snapshot)
    variances = np.asarray(variances).reshape(pool.candidate_count, pool.scan_count)
    references = []
    diagnostics = []
    for curve in variances:
        if not np.all(np.isfinite(curve)):
            references.append(None)
            diagnostics.append(dict(status="incomplete_scan"))
            continue
        result = reconstruct_beam_matrix(projections, np.sqrt(curve) * 1e-3)
        diagnostics.append(asdict(result))
        if not result.valid:
            references.append(None)
        else:
            references.append(result.geometric_emittance_m_rad * beta_gamma(pool.energy_mev) * 1e6)
    return pool, variances, np.asarray(references, dtype=float), dict(
        references_mm_mrad=references, reconstruction=diagnostics, measurements=measurements,
        full_scan_observations=sum(row["state"] == "observed" for row in measurements),
        full_scan_attempts=pool.query_count, reference_calculations=len(vm.calculations),
        reference_type=config["reference"],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    raw_config = args.config.read_bytes()
    config = json.loads(raw_config)
    if (config["schema"] != "half_infobax_p2_b2_benchmark_v1" or config["policies"] != ["infobax", "random"]
            or len(config["seeds"]) != len(set(config["seeds"]))
            or not 0 < config["initial_observations"] < config["total_observations"]
            <= len(config["workpoints"]) * len(config["scan_values"])):
        raise ValueError("invalid bounded benchmark configuration")
    (args.output_dir / "config.json").write_bytes(raw_config)
    source_paths = [Path(__file__), REPO_ROOT / "scripts/check_emit_infobax_vm.py",
                    REPO_ROOT / "src/shared/beam_matrix.py",
                    REPO_ROOT / "src/shared/machine_profile/model_backend.py",
                    *sorted((REPO_ROOT / "src/shared/elegant_backend").glob("*.py")),
                    *sorted((REPO_ROOT / "src/optimization/infobax").glob("*.py")),
                    *sorted((REPO_ROOT / "src/apps/emit_measure").glob("infobax*.py"))]
    metadata = dict(config_sha256=hashlib.sha256(raw_config).hexdigest(),
                    code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
                    source_sha256={str(path.relative_to(REPO_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in source_paths},
                    python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
                    elegant_binary_sha256=hashlib.sha256(Path(shutil.which("elegant")).read_bytes()).hexdigest(),
                    numerical_config=InfoBAXConfig().to_dict(), ioc_access=False)
    (args.output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    rows, failures, references = [], [], {}
    for case in config["cases"]:
        print(f"Generating frozen full-scan reference: {case['id']}", flush=True)
        try:
            with TemporaryDirectory(prefix="infobax-reference-") as temporary:
                pool, variances, truth, evidence = generate_case(Path(temporary), config, case)
            references[case["id"]] = evidence
            (args.output_dir / f"{case['id']}_reference.json").write_text(json.dumps(evidence, indent=2) + "\n")
            if not np.all(np.isfinite(truth)):
                raise RuntimeError("full-scan reference is incomplete or nonphysical; see reference evidence")
        except Exception as error:
            failures.append(dict(case=case["id"], error=f"{type(error).__name__}: {error}"))
            continue
        for seed in config["seeds"]:
            initial_queries = []
            for policy in config["policies"]:
                evaluator = FinitePoolEvaluator(pool, variances, truth, case_id=case["id"], seed=seed)
                core_config = InfoBAXConfig(seed=seed, initial_actions=config["initial_observations"])
                row = run_policy(pool, core_config, config["total_observations"], policy, evaluator, case_id=case["id"])
                rows.append(row)
                initial_queries.append(row["queries"][:config["initial_observations"]])
                print(f"case={case['id']} seed={seed} policy={policy} status={row['status']} observations={row['observations']}", flush=True)
            if [(q["query_index"], q["variance_mm2"]) for q in initial_queries[0]] != [
                (q["query_index"], q["variance_mm2"]) for q in initial_queries[1]
            ]:
                raise RuntimeError("paired initial observations differ")
            (args.output_dir / "trajectories.json").write_text(json.dumps(rows, indent=2) + "\n")
    summary = paired_summary(rows, config)
    summary["by_case"] = {
        case["id"]: paired_summary([row for row in rows if row["case"] == case["id"]],
                                   {**config, "cases": [case]})
        for case in config["cases"]
    }
    summary["gates"]["all_full_scan_references_physical"] = not failures
    summary.update(reference_failures=failures, config_sha256=metadata["config_sha256"],
                   reference_frames=sum(item["full_scan_observations"] for item in references.values()),
                   reference_label=config["reference"], ioc_access=False,
                   status="passed" if all(summary["gates"].values()) else "BENCHMARK-STOP",
                   gui_acceptance="eligible for separate IOC gate" if all(summary["gates"].values()) else "blocked by frozen benchmark gates")
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
