#!/usr/bin/env python3
"""P2-B4 VM count-variance calibration followed by a gated M0 comparison."""

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

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import numpy as np
import scipy
from scripts.diagnose_emit_infobax_measurement import (
    DiagnosticBeam, csv_rows, gate_below, relative_error, sha256, write_json,
)
from scripts.benchmark_emit_infobax import paired_summary, run_policy
from half_linac.src.apps.emit_measure.infobax_measurement import RmsVarianceConfig, measure_count_variance
from half_linac.src.apps.emit_measure.infobax_vm import (
    FiniteWorkpointTable, MachineVariable, build_half_vm_infobax_pool,
)
from half_linac.src.optimization.infobax import InfoBAXConfig, PointObservation
from half_linac.src.optimization.infobax.core import stable_seed
from half_linac.src.shared.beam_matrix import reconstruct_beam_matrix
from half_linac.src.shared.machine_profile import load_app_context

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_rms_calibration.json"
FROZEN = REPO_ROOT / "src/optimization/infobax/benchmarks"


class CalibratedBeam(DiagnosticBeam):
    def __init__(self, root, config, case):
        super().__init__(root, config, case)
        self.rms_config = RmsVarianceConfig(**config["measurement"])

    def measure(self, workpoint, scan_value, seed):
        row = super().measure(workpoint, scan_value, seed)
        if row["status"] == "observed":
            try:
                raw = self.parser.load_watch_image(
                    self.root / "PRF07.out", pixel_shape=self.geometry.shape,
                    pixel_width_mm=self.geometry.pixel_width_mm,
                )
                result = measure_count_variance(
                    raw, pixel_shape=self.geometry.shape, pixel_width_mm=self.geometry.pixel_width_mm,
                    config=self.rms_config,
                )
                row.update(corrected_variance_mm2=result.variance_mm2, noise_std_mm2=result.noise_std_mm2,
                           measurement=result.to_dict(),
                           corrected_variance_relative_error=relative_error(result.variance_mm2, row["particle_rms"]))
            except (TypeError, ValueError) as error:
                row.update(status="failed", error=f"{type(error).__name__}: {error}")
        return row


def references_for(rows, projections, config):
    result_rows = []
    for work in config["workpoints"]:
        curve = [next(row for row in rows if row["workpoint"] == work and row["scan_value"] == k)
                 for k in config["scan_values"]]
        row = dict(case=rows[0]["case"], workpoint=work, status="incomplete_scan",
                   emittance_mm_mrad=None, entrance_reference_mm_mrad=None, relative_error=None)
        if all(r["status"] == "observed" for r in curve):
            result = reconstruct_beam_matrix(
                [projections[float(k)] for k in config["scan_values"]],
                np.sqrt([r["corrected_variance_mm2"] for r in curve])*1e-3,
            )
            row.update(status=result.status, message=result.message, moments=result.moments)
            entrance = float(np.mean([r["entrance_emittance_mm_mrad"] for r in curve]))
            if result.valid:
                value = result.geometric_emittance_m_rad * np.mean([r["entrance_beta_gamma"] for r in curve]) * 1e6
                row.update(emittance_mm_mrad=float(value), entrance_reference_mm_mrad=entrance,
                           relative_error=relative_error(value, entrance))
        result_rows.append(row)
    return result_rows


def calibration_summary(primary, training, validation, references, config):
    residuals, by_point = [], []
    for case in config["cases"]:
        for work in config["repeat_workpoints"]:
            for scan in config["repeat_scan_values"]:
                train = [r for r in training if (r["case"], r["workpoint"], r["scan_value"]) == (case["id"], work, scan)]
                heldout = [r for r in validation if (r["case"], r["workpoint"], r["scan_value"]) == (case["id"], work, scan)]
                valid = [r for r in train if r["status"] == "observed"]
                if valid:
                    mean = float(np.mean([r["corrected_variance_mm2"] for r in valid]))
                    mean_variance = sum(r["noise_std_mm2"]**2 for r in valid)/len(valid)**2
                    scores = [(r["corrected_variance_mm2"]-mean)/np.sqrt(r["noise_std_mm2"]**2+mean_variance)
                              for r in heldout if r["status"] == "observed"]
                    residuals.extend(float(z) for z in scores)
                else:
                    mean, scores = None, []
                by_point.append(dict(case=case["id"], workpoint=work, scan_value=scan,
                                     calibration_mean_mm2=mean, calibration_valid=len(valid),
                                     heldout_valid=len(scores), standardized_residuals=[float(z) for z in scores]))
    expected_primary = len(config["cases"])*len(config["workpoints"])*len(config["scan_values"])
    points = len(config["cases"])*len(config["repeat_workpoints"])*len(config["repeat_scan_values"])
    all_valid = (
        len(primary) == expected_primary
        and len(training) == points*len(config["calibration_particle_seeds"])
        and len(validation) == points*len(config["validation_particle_seeds"])
        and all(r["status"] == "observed" for r in primary+training+validation)
    )
    maximum_primary = max((r["corrected_variance_relative_error"] for r in primary if r["status"] == "observed"), default=None)
    maximum_validation = max((r["corrected_variance_relative_error"] for r in validation if r["status"] == "observed"), default=None)
    maximum_emittance = max((r["relative_error"] for r in references if r["status"] == "valid"), default=None)
    coverage = float(np.mean(np.abs(residuals) <= 1.959963984540054)) if residuals else None
    mean = float(np.mean(residuals)) if residuals else None
    mean_square = float(np.mean(np.square(residuals))) if residuals else None
    limits = config["calibration_gates"]
    gates = dict(
        all_frames_valid=all_valid,
        primary_variance_closure=gate_below(maximum_primary, limits["maximum_primary_variance_relative_error"]),
        heldout_variance_closure=gate_below(maximum_validation, limits["maximum_validation_variance_relative_error"]),
        all_references_physical=len(references) == len(config["cases"])*len(config["workpoints"])
                                and all(r["status"] == "valid" for r in references),
        emittance_closure=gate_below(maximum_emittance, limits["maximum_emittance_relative_error"]),
        heldout_noise_coverage=coverage is not None and coverage >= limits["minimum_heldout_95_interval_coverage"],
        heldout_noise_mean=gate_below(abs(mean) if mean is not None else None, limits["maximum_absolute_heldout_standardized_mean"]),
        heldout_noise_scale=mean_square is not None and limits["heldout_standardized_mean_square_range"][0]
                           <= mean_square <= limits["heldout_standardized_mean_square_range"][1],
    )
    return dict(status="passed" if all(gates.values()) else "CALIBRATION-STOP", gates=gates,
                primary_frames=len(primary), calibration_frames=len(training), validation_frames=len(validation),
                physical_references=sum(r["status"] == "valid" for r in references),
                max_primary_variance_relative_error=maximum_primary,
                max_validation_variance_relative_error=maximum_validation,
                max_emittance_relative_error=maximum_emittance,
                heldout_standardized_mean=mean, heldout_standardized_mean_square=mean_square,
                heldout_95_interval_coverage=coverage, noise_validation_points=by_point,
                failures=[r for r in primary+training+validation if r["status"] != "observed"])


def frame_seed(case_id, policy_seed, index):
    return 2*(stable_seed("b4_particle", case_id, policy_seed, index) % 1000000000)+1


def validate_config(config):
    train, heldout = config["calibration_particle_seeds"], config["validation_particle_seeds"]
    options = config["benchmark"]
    if (config["schema"] != "half_infobax_p2_b4_rms_calibration_v1"
            or set(train) & set(heldout) or len(set(train)) != len(train)
            or len(set(heldout)) != len(heldout)
            or config["primary_particle_seed"] in heldout
            or config["measurement_method"] != "rms_count_variance"
            or options["policies"] != ["infobax", "random"]
            or not 0 < options["initial_observations"] < options["total_observations"]
            <= len(config["workpoints"])*len(config["scan_values"])):
        raise ValueError("invalid calibration configuration or particle-seed leakage")
    RmsVarianceConfig(**config["measurement"])


class PositiveFrameEvaluator:
    """Only this evaluator sees the frozen full-scan emittance reference."""

    def __init__(self, pool, reference, draw):
        self.pool, self._reference, self._draw = pool, np.asarray(reference), draw
        self._revealed = set()

    def reveal(self, index):
        self.pool.query_parts(index)
        if index in self._revealed:
            raise ValueError("duplicate frame query")
        self._revealed.add(index)
        row = self._draw(index)
        if row["status"] != "observed":
            raise ValueError(f"frame quality failure: {row['error']}")
        return PointObservation(index, row["corrected_variance_mm2"], row["noise_std_mm2"])

    def evaluate(self, recommendation):
        index = recommendation.recommended_candidate_index
        if index is None:
            return dict(relative_regret=None, best_hit=False)
        return dict(relative_regret=float(self._reference[index]/self._reference.min()-1),
                    best_hit=bool(index == int(np.argmin(self._reference))))


def benchmark_case(beam, references, config, output, case_id):
    options = config["benchmark"]
    context = load_app_context("emit_measure", machine_id="half", control_backend="vm")
    table = FiniteWorkpointTable(tuple(f"workpoint-{i}" for i in range(len(config["workpoints"]))),
                                (MachineVariable("QT01"),), np.asarray(config["workpoints"])[:, None])

    class CachedMaps:
        def get_map(self, _first, _second, **kwargs):
            matrix = np.eye(4)
            matrix[0, :2] = beam.projections[float(kwargs["k1"])]
            return matrix

    pool = build_half_vm_infobax_pool(
        context, table, scan_quad="QT02", screen="PRF07", plane="x", scan_values=config["scan_values"],
        energy_mev=config["energy_mev"], observation_noise_std_mm2=options["forecast_noise_std_mm2"],
        model_backend=CachedMaps(), measurement_method=config["measurement_method"], rms_config=beam.rms_config,
    )
    truth = [r["emittance_mm_mrad"] for r in references]
    rows, frames = [], {}
    for seed in options["seeds"]:
        def draw(index):
            key = (seed, index)
            if key not in frames:
                candidate, scan = pool.query_parts(index)
                row = beam.measure(config["workpoints"][candidate], config["scan_values"][scan], frame_seed(case_id, seed, index))
                frames[key] = dict(row, policy_seed=seed, query_index=index)
            return frames[key]
        pair = []
        for policy in options["policies"]:
            evaluator = PositiveFrameEvaluator(pool, truth, draw)
            row = run_policy(pool, InfoBAXConfig(seed=seed, initial_actions=options["initial_observations"]),
                             options["total_observations"], policy, evaluator, case_id=case_id)
            rows.append(row)
            pair.append([(q["query_index"], q["variance_mm2"], q["noise_std_mm2"])
                         for q in row["queries"][:options["initial_observations"]]])
            print(f"B4 case={case_id} seed={seed} policy={policy} observations={row['observations']} status={row['status']}", flush=True)
        common = min(len(pair[0]), len(pair[1]))
        if pair[0][:common] != pair[1][:common]:
            raise RuntimeError("paired initial frames differ")
        write_json(output / f"{case_id}_trajectories.json", rows)
        csv_rows(output / f"{case_id}_frames.csv", list(frames.values()))
    return rows, len(frames)


def run(config, output):
    primary, training, validation, references, roots = [], [], [], [], {}
    # Keep the independent case workspaces alive until the conditional comparison ends.
    from contextlib import ExitStack
    with ExitStack() as stack:
        for case in config["cases"]:
            root = Path(stack.enter_context(TemporaryDirectory(prefix="infobax-b4-")))
            beam = CalibratedBeam(root, config, case)
            roots[case["id"]] = beam
            cache, case_rows = {}, []
            print(f"B4 primary calibration: {case['id']}", flush=True)
            for work in config["workpoints"]:
                for scan in config["scan_values"]:
                    row = beam.measure(work, scan, config["primary_particle_seed"])
                    cache[(work, scan, config["primary_particle_seed"])] = row
                    case_rows.append(row)
            primary.extend(case_rows)
            references.extend(references_for(case_rows, beam.projections, config))
            for label, seeds, target in (
                ("calibration", config["calibration_particle_seeds"], training),
                ("heldout", config["validation_particle_seeds"], validation),
            ):
                print(f"B4 {label} particle populations: {case['id']}", flush=True)
                for work in config["repeat_workpoints"]:
                    for scan in config["repeat_scan_values"]:
                        for seed in seeds:
                            key = (work, scan, seed)
                            if key not in cache:
                                cache[key] = beam.measure(work, scan, seed)
                            target.append(cache[key])
            for name, rows in (("primary.csv", primary), ("calibration.csv", training),
                               ("validation.csv", validation), ("references.csv", references)):
                csv_rows(output / name, rows)
        calibration = calibration_summary(primary, training, validation, references, config)
        write_json(output / "calibration_summary.json", calibration)
        comparison, unique_frames = [], 0
        if calibration["status"] == "passed":
            for case_id in config["benchmark"]["cases"]:
                case_references = [r for r in references if r["case"] == case_id]
                rows, actual = benchmark_case(roots[case_id], case_references, config, output, case_id)
                comparison.extend(rows)
                unique_frames += actual
        comparison_config = {**config["benchmark"], "cases": [{"id": name} for name in config["benchmark"]["cases"]]}
        benchmark = paired_summary(comparison, comparison_config) if comparison else None
        if benchmark is not None:
            benchmark["by_case"] = {
                case_id: paired_summary([r for r in comparison if r["case"] == case_id],
                                        {**comparison_config, "cases": [{"id": case_id}]})
                for case_id in config["benchmark"]["cases"]
            }
            write_json(output / "trajectories.json", comparison)
        status = "CALIBRATION-STOP" if calibration["status"] != "passed" else (
            "passed" if all(benchmark["gates"].values()) else "BENCHMARK-STOP"
        )
        summary = dict(stage="P2-B4", status=status, calibration_status=calibration["status"],
                       calibration_gates=calibration["gates"], benchmark=benchmark,
                       benchmark_unique_frames=unique_frames,
                       benchmark_charged_observations=sum(r["observations"] for r in comparison),
                       tracking_attempts=sum(beam.attempts for beam in roots.values()),
                       ioc_access=False, gp_or_acquisition_changes=False,
                       gui_acceptance="requires independent IOC acceptance" if status == "passed" else "blocked")
        write_json(output / "summary.json", summary)
        print(json.dumps(summary, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raw = args.config.read_bytes()
    config = json.loads(raw)
    validate_config(config)
    if config["predecessor_config_sha256"] != sha256(FROZEN / "p2_b3/config.json"):
        raise ValueError("frozen predecessor configuration does not match")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "config.json").write_bytes(raw)
    paths = [Path(__file__), REPO_ROOT / "scripts/check_emit_infobax_vm.py",
             REPO_ROOT / "scripts/diagnose_emit_infobax_measurement.py", REPO_ROOT / "scripts/benchmark_emit_infobax.py",
             REPO_ROOT / "src/shared/beam_matrix.py", REPO_ROOT / "src/shared/elegant_backend/parser.py",
             REPO_ROOT / "src/shared/beam_diagnostics/image_fit.py",
             *sorted((REPO_ROOT / "src/apps/emit_measure").glob("infobax*.py")),
             *sorted((REPO_ROOT / "src/optimization/infobax").glob("*.py"))]
    frozen_hashes = {str(path.relative_to(REPO_ROOT)): sha256(path)
                     for stage in ("p2_b2", "p2_b3") for path in sorted((FROZEN / stage).glob("*")) if path.is_file()}
    write_json(args.output_dir / "metadata.json", dict(
        config_sha256=hashlib.sha256(raw).hexdigest(), source_sha256={str(path.relative_to(REPO_ROOT)): sha256(path) for path in paths},
        predecessor_evidence_sha256=frozen_hashes,
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
        elegant_binary_sha256=sha256(shutil.which("elegant")), forecast_noise=config["benchmark"]["forecast_noise_interpretation"],
    ))
    run(config, args.output_dir)
    if any(sha256(REPO_ROOT/path) != digest for path, digest in frozen_hashes.items()):
        raise RuntimeError("frozen predecessor evidence changed")


if __name__ == "__main__":
    main()
