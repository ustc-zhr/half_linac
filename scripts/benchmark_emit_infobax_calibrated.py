#!/usr/bin/env python3
"""P2-B8: gated, frozen M0 comparison with actual calibrated count frames."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import math
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
from scripts.benchmark_emit_infobax import paired_summary, run_policy
from scripts.calibrate_emit_infobax_rms import PositiveFrameEvaluator
from scripts.calibrate_emit_infobax_weighted_rms import read_rows
from scripts.calibrate_emit_infobax_expanded_noise import prior_particle_seeds
from scripts.diagnose_emit_infobax_measurement import csv_rows, sha256, write_json
from scripts.validate_emit_infobax_reference_precision import PrecisionBeam
from half_linac.src.apps.emit_measure.infobax_vm import (
    FiniteWorkpointTable, MachineVariable, build_half_vm_infobax_pool,
)
from half_linac.src.optimization.infobax import InfoBAXConfig
from half_linac.src.optimization.infobax.core import stable_seed
from half_linac.src.shared.machine_profile import load_app_context

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_calibrated_benchmark.json"
FROZEN = REPO_ROOT / "src/optimization/infobax/benchmarks"
PREVIOUS_STAGES = ("p2_b2", "p2_b3", "p2_b4", "p2_b4_1", "p2_b5", "p2_b6", "p2_b7")


def frame_seed(case, policy_seed, query_index):
    return 2*(stable_seed("b8_particle", case, policy_seed, query_index) % 1000000000)+1


def seed_schedule(config):
    return [dict(case=case["id"], policy_seed=seed, query_index=index,
                 particle_seed=frame_seed(case["id"], seed, index))
            for case in config["cases"] for seed in config["seeds"]
            for index in range(len(config["workpoints"])*len(config["scan_values"]))]


def validate_config(config, ioc):
    previous = json.loads((FROZEN / "p2_b7/config.json").read_text())
    old = json.loads((FROZEN / "p2_b4_1/config.json").read_text())["benchmark"]
    selected_cases = [case for case in previous["cases"] if case["id"] in old["cases"]]
    if (config["schema"] != "half_infobax_p2_b8_calibrated_benchmark_v1" or config["stage"] != "P2-B8"
            or config["cases"] != selected_cases or config["particles"] != 100000
            or config["reference_arm"] != "native_100k" or config["linear_resolution_factors"] != [1]
            or config["measurement_method"] != "rms_count_variance" or config["workers"] != 2):
        raise ValueError("invalid bounded calibrated benchmark settings")
    for key in ("workpoints", "scan_values", "energy_mev", "measurement"):
        if config[key] != previous[key]:
            raise ValueError(f"accepted physical settings changed: {key}")
    for key in ("seeds", "policies", "initial_observations", "total_observations", "forecast_noise_std_mm2",
                "bootstrap_seed", "bootstrap_samples", "gates"):
        if config[key] != old[key]:
            raise ValueError(f"bounded benchmark budget or gates changed: {key}")
    accepted = json.loads((FROZEN / "p2_b7/summary.json").read_text())
    fit = json.loads((FROZEN / "p2_b7/noise_scale_fit.json").read_text())["noise_scale"]
    if accepted["status"] != "NOISE-PASS" or not all(accepted["calibration_gates"].values()):
        raise ValueError("B7 noise acceptance is required")
    if (ioc.get("status") != "passed" or ioc.get("measurement_method") != "rms_count_variance"
            or ioc.get("particles") != 100000 or ioc.get("noise_scale") != fit
            or ioc.get("observations") != 7 or ioc.get("stale_observations") != 0
            or not all(ioc.get(key) is True for key in ("paused_and_resumed", "restored", "stale_frame_rejected",
                                                       "incompatible_scale_checkpoint_rejected", "processes_cleaned_up"))
            or not ioc.get("prefix", "").startswith("INFOBAX:TEST:")
            or not ioc.get("ca_address", "").startswith("127.0.0.1:")):
        raise ValueError("matching actual isolated IOC acceptance is required")
    audits = ioc.get("live_frame_audits", [])
    if len(audits) != 7 or len({row["query_index"] for row in audits}) != 7 or any(
        row["count"] != 100000 or row["noise_scale"] != fit
        or not math.isclose(row["scaled_noise_std_mm2"], row["unscaled_noise_std_mm2"]*fit, rel_tol=1e-12)
        for row in audits
    ):
        raise ValueError("all seven matching live IOC frame audits are required")
    prior = prior_particle_seeds() | set(previous["calibration_particle_seeds"]+previous["validation_particle_seeds"])
    planned = [row["particle_seed"] for row in seed_schedule(config)]
    if len(set(planned)) != len(planned) or set(planned) & prior:
        raise ValueError("query-indexed particle seeds collide or leak prior populations")


class BenchmarkBeam(PrecisionBeam):
    def measure(self, work, scan, seed):
        row = self.measure_pair(work, scan, seed)[0]
        if row["status"] == "observed":
            row.update(corrected_variance_mm2=row["variance_mm2"],
                       corrected_variance_relative_error=row["variance_relative_error"])
        return row


class PairedFrames:
    """Cache revealed frames by query, with no policy-dependent particle draw."""

    def __init__(self, beam, config, case_id):
        self.beam, self.config, self.case_id = beam, config, case_id
        self.rows = {}

    def reveal(self, policy_seed, query_index):
        key = (policy_seed, query_index)
        if key not in self.rows:
            candidate, scan = divmod(query_index, len(self.config["scan_values"]))
            row = self.beam.measure(self.config["workpoints"][candidate], self.config["scan_values"][scan],
                                    frame_seed(self.case_id, policy_seed, query_index))
            self.rows[key] = dict(row, policy_seed=policy_seed, query_index=query_index)
        return self.rows[key]


def initial_pair_equal(pair, initial_count):
    traces = [[(q["query_index"], q["variance_mm2"], q["noise_std_mm2"])
               for q in row["queries"][:initial_count]] for row in pair]
    return len(traces) == 2 and len(traces[0]) == len(traces[1]) == initial_count and traces[0] == traces[1]


def run_case(config, case, output):
    case_id = case["id"]
    refs = [r for r in read_rows(FROZEN / "p2_b7/references.csv") if r["case"] == case_id]
    refs = [next(r for r in refs if r["workpoint"] == work) for work in config["workpoints"]]
    if any(r["status"] != "valid" or not r["emittance_mm_mrad"] > 0 or r["relative_error"] > 0.05 for r in refs):
        raise ValueError("accepted full-scan references are incomplete or nonphysical")
    scale = json.loads((FROZEN / "p2_b7/noise_scale_fit.json").read_text())["noise_scale"]
    with TemporaryDirectory(prefix="infobax-b8-") as temporary:
        beam = BenchmarkBeam(Path(temporary), config, case, config["particles"], scale)
        context = load_app_context("emit_measure", machine_id="half", control_backend="vm")
        table = FiniteWorkpointTable(tuple(f"workpoint-{i}" for i in range(len(config["workpoints"]))),
                                    (MachineVariable("QT01"),), np.array(config["workpoints"])[:, None])
        class CachedMaps:
            def get_map(self, _first, _second, **kwargs):
                matrix = np.eye(4)
                matrix[0, :2] = beam.projections[float(kwargs["k1"])]
                return matrix
        pool = build_half_vm_infobax_pool(
            context, table, scan_quad="QT02", screen="PRF07", plane="x", scan_values=config["scan_values"],
            energy_mev=config["energy_mev"], observation_noise_std_mm2=config["forecast_noise_std_mm2"],
            model_backend=CachedMaps(), measurement_method=config["measurement_method"], rms_config=beam.rms_config)
        frames = PairedFrames(beam, config, case_id)
        rows, audits = [], []
        for seed in config["seeds"]:
            pair = []
            for policy in config["policies"]:
                evaluator = PositiveFrameEvaluator(pool, [r["emittance_mm_mrad"] for r in refs],
                                                   lambda index: frames.reveal(seed, index))
                row = run_policy(pool, InfoBAXConfig(seed=seed, initial_actions=config["initial_observations"]),
                                 config["total_observations"], policy, evaluator, case_id=case_id)
                rows.append(row)
                pair.append(row)
                print(f"B8 case={case_id} seed={seed} policy={policy} observations={row['observations']} status={row['status']}", flush=True)
            audits.append(dict(case=case_id, seed=seed, paired_initial_equal=initial_pair_equal(pair, config["initial_observations"])))
            write_json(output / f"{case_id}_trajectories.json", rows)
            csv_rows(output / f"{case_id}_frames.csv", list(frames.rows.values()))
            write_json(output / f"{case_id}_pairing.json", audits)
        return rows, audits, beam.attempts, len(frames.rows)


def comparison_summary(rows, pairing, config):
    result = paired_summary(rows, config)
    result["by_case"] = {case["id"]: paired_summary([r for r in rows if r["case"] == case["id"]],
                                                  dict(config, cases=[case])) for case in config["cases"]}
    expected = {(case["id"], seed, policy) for case in config["cases"] for seed in config["seeds"] for policy in config["policies"]}
    keys = [(r["case"], r["seed"], r["policy"]) for r in rows]
    pair_keys = [(r["case"], r["seed"]) for r in pairing]
    expected_pairs = {(case["id"], seed) for case in config["cases"] for seed in config["seeds"]}
    result["gates"].update(trajectory_grid_complete=len(keys) == len(expected) and set(keys) == expected,
                            paired_initial_measurements_identical=len(pair_keys) == len(expected_pairs)
                            and set(pair_keys) == expected_pairs and all(r["paired_initial_equal"] for r in pairing))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ioc-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_bytes())
    validate_config(config, json.loads(args.ioc_summary.read_text()))
    if (config["predecessor_config_sha256"] != sha256(FROZEN / "p2_b7/config.json")
            or config["ioc_acceptance_sha256"] != sha256(args.ioc_summary)):
        raise ValueError("frozen predecessor or IOC acceptance mismatch")
    for stage in PREVIOUS_STAGES:
        folder = FROZEN / stage
        index = json.loads((folder / "result_index.json").read_text())
        if any(sha256(folder / p) != h for p, h in index.get("artifacts_sha256", {}).items()):
            raise ValueError(f"frozen predecessor evidence mismatch: {stage}")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.config, args.output_dir / "config.json")
    shutil.copyfile(args.ioc_summary, args.output_dir / "ioc_acceptance.json")
    csv_rows(args.output_dir / "particle_seed_schedule.csv", seed_schedule(config))
    sources = [Path(__file__), REPO_ROOT / "scripts/check_emit_infobax_ioc.py",
               REPO_ROOT / "scripts/check_emit_infobax_vm.py", REPO_ROOT / "scripts/benchmark_emit_infobax.py",
               REPO_ROOT / "scripts/calibrate_emit_infobax_rms.py", REPO_ROOT / "scripts/calibrate_emit_infobax_weighted_rms.py",
               REPO_ROOT / "scripts/calibrate_emit_infobax_100k_noise.py", REPO_ROOT / "scripts/calibrate_emit_infobax_expanded_noise.py",
               REPO_ROOT / "scripts/validate_emit_infobax_reference_precision.py", REPO_ROOT / "scripts/diagnose_emit_infobax_measurement.py",
               REPO_ROOT / "src/shared/beam_matrix.py", REPO_ROOT / "src/shared/elegant_backend/parser.py",
               REPO_ROOT / "src/shared/machine_profile/model_backend.py", REPO_ROOT / "src/shared/elegant_backend/publisher.py",
               REPO_ROOT / "src/virtual_machine/common/start_VM.py", REPO_ROOT / "src/softIOC/pv_server.py",
               *sorted((REPO_ROOT / "src/optimization/infobax").glob("*.py")),
               *sorted((REPO_ROOT / "src/apps/emit_measure").glob("infobax*.py"))]
    source_hashes = {str(p.relative_to(REPO_ROOT)): sha256(p) for p in sources}
    prior = {str(p.relative_to(REPO_ROOT)): sha256(p) for stage in PREVIOUS_STAGES
             for p in sorted((FROZEN / stage).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    write_json(args.output_dir / "metadata.json", dict(
        config_sha256=sha256(args.config), source_sha256=source_hashes, predecessor_evidence_sha256=prior,
        numerical_config=InfoBAXConfig().to_dict(), python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
        elegant_binary_sha256=sha256(shutil.which("elegant")), ioc_summary_source=str(args.ioc_summary),
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        benchmark_ioc_access=False, runtime_gate="actual private loopback IOC; see ioc_acceptance.json"))
    rows, pairing, tracking, unique = [], [], 0, 0
    with ProcessPoolExecutor(max_workers=config["workers"], mp_context=multiprocessing.get_context("spawn")) as executor:
        jobs = [executor.submit(run_case, config, case, args.output_dir) for case in config["cases"]]
        for future in as_completed(jobs):
            result, audits, attempts, count = future.result()
            rows.extend(result); pairing.extend(audits); tracking += attempts; unique += count
    rows.sort(key=lambda r: ([c["id"] for c in config["cases"]].index(r["case"]), r["seed"], config["policies"].index(r["policy"])))
    pairing.sort(key=lambda r: (r["case"], r["seed"]))
    write_json(args.output_dir / "trajectories.json", rows)
    write_json(args.output_dir / "pairing.json", pairing)
    summary = comparison_summary(rows, pairing, config)
    summary["gates"]["all_full_scan_references_physical"] = True
    summary.update(stage="P2-B8", status="BENCHMARK-PASS" if all(summary["gates"].values()) else "BENCHMARK-STOP",
                   ioc_status="passed", new_tracking_attempts=tracking, benchmark_unique_frames=unique,
                   charged_observations=sum(r["observations"] for r in rows), optimizer_runs=len(rows),
                   reference_source="B7 accepted weighted primary full scans, evaluator only", forecast_noise_std_mm2=config["forecast_noise_std_mm2"],
                   gp_or_acquisition_changes=False, benchmark_ioc_access=False,
                   gui_acceptance="separate review required" if all(summary["gates"].values()) else "blocked by benchmark gates")
    write_json(args.output_dir / "summary.json", summary)
    if any(sha256(REPO_ROOT / p) != h for p, h in {**source_hashes, **prior}.items()):
        raise RuntimeError("frozen experiment source or prior evidence changed")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
