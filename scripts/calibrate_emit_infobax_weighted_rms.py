#!/usr/bin/env python3
"""P2-B4.1: one frozen weighted-reference/global-noise-scale continuation."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
import csv
from dataclasses import replace
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
from scripts.calibrate_emit_infobax_rms import (
    CalibratedBeam, benchmark_case, calibration_summary, validate_config,
)
from scripts.benchmark_emit_infobax import paired_summary
from scripts.diagnose_emit_infobax_measurement import csv_rows, relative_error, sha256, write_json
from half_linac.src.shared.beam_matrix import reconstruct_beam_matrix

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_weighted_rms_calibration.json"
FROZEN = REPO_ROOT / "src/optimization/infobax/benchmarks"
INPUT = FROZEN / "p2_b4"


def read_rows(path):
    def parse(value):
        if value == "":
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    with path.open() as handle:
        return [{key: parse(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def training_noise_scale(training, config):
    ratios = []
    for case in config["cases"]:
        for work in config["repeat_workpoints"]:
            for scan in config["repeat_scan_values"]:
                rows = [r for r in training if (r["case"], r["workpoint"], r["scan_value"]) == (case["id"], work, scan)]
                if (len(rows) != len(config["calibration_particle_seeds"])
                        or any(row["status"] != "observed" for row in rows)):
                    raise ValueError("incomplete training populations; no noise-scale fit")
                empirical = np.var([r["corrected_variance_mm2"] for r in rows], ddof=1)
                predicted = np.mean([r["noise_std_mm2"]**2 for r in rows])
                ratios.append(float(empirical / predicted))
    scale = float(np.sqrt(np.mean(ratios)))
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("training noise scale is nonpositive")
    return dict(noise_scale=scale, point_variance_ratios=ratios, fitted_points=len(ratios),
                data_source="frozen B4 calibration.csv only; no held-out observations")


def rescale(rows, factor):
    rows = copy.deepcopy(rows)
    for row in rows:
        if row["status"] == "observed":
            raw = row["noise_std_mm2"]
            row.update(unscaled_noise_std_mm2=raw, noise_std_mm2=raw*factor)
            row["measurement"].update(noise_std_mm2=raw*factor, noise_scale=factor)
    return rows


def weighted_references(rows, projections, config):
    output = []
    for work in config["workpoints"]:
        curve = [next(r for r in rows if r["workpoint"] == work and r["scan_value"] == scan)
                 for scan in config["scan_values"]]
        row = dict(case=rows[0]["case"], workpoint=work, status="incomplete_scan",
                   emittance_mm_mrad=None, entrance_reference_mm_mrad=None, relative_error=None)
        if all(r["status"] == "observed" for r in curve):
            variance = np.array([r["corrected_variance_mm2"] for r in curve])
            noise = np.array([r["noise_std_mm2"] for r in curve])
            result = reconstruct_beam_matrix(
                [projections[float(k)] for k in config["scan_values"]],
                np.sqrt(variance)*1e-3, noise/(2*np.sqrt(variance))*1e-3,
            )
            row.update(status=result.status, solver=result.solver, moments=result.moments,
                       condition_number=result.condition_number, reduced_chi_squared=result.reduced_chi_squared)
            if result.valid:
                value = float(result.geometric_emittance_m_rad*np.mean([r["entrance_beta_gamma"] for r in curve])*1e6)
                reference = float(np.mean([r["entrance_emittance_mm_mrad"] for r in curve]))
                row.update(emittance_mm_mrad=value, entrance_reference_mm_mrad=reference,
                           relative_error=relative_error(value, reference))
        output.append(row)
    return output


def run(config, output):
    fit = training_noise_scale(read_rows(INPUT / "calibration.csv"), config)
    write_json(output / "noise_scale_fit.json", fit)
    primary = rescale(read_rows(INPUT / "primary.csv"), fit["noise_scale"])
    training = rescale(read_rows(INPUT / "calibration.csv"), fit["noise_scale"])
    validation, references, beams = [], [], {}
    with ExitStack() as stack:
        for case in config["cases"]:
            root = Path(stack.enter_context(TemporaryDirectory(prefix="infobax-b41-")))
            beam = CalibratedBeam(root, config, case)
            beam.rms_config = replace(beam.rms_config, noise_scale=fit["noise_scale"])
            beams[case["id"]] = beam
            references.extend(weighted_references([r for r in primary if r["case"] == case["id"]], beam.projections, config))
            print(f"B4.1 independent validation: {case['id']}", flush=True)
            for work in config["repeat_workpoints"]:
                for scan in config["repeat_scan_values"]:
                    for seed in config["validation_particle_seeds"]:
                        validation.append(beam.measure(work, scan, seed))
            csv_rows(output / "validation.csv", validation)
            csv_rows(output / "references.csv", references)
        csv_rows(output / "primary.csv", primary)
        csv_rows(output / "calibration.csv", training)
        calibration = calibration_summary(primary, training, validation, references, config)
        write_json(output / "calibration_summary.json", calibration)
        trajectories, actual_frames = [], 0
        if calibration["status"] == "passed":
            for case_id in config["benchmark"]["cases"]:
                rows, frames = benchmark_case(beams[case_id], [r for r in references if r["case"] == case_id], config, output, case_id)
                trajectories.extend(rows)
                actual_frames += frames
        options = {**config["benchmark"], "cases": [{"id": name} for name in config["benchmark"]["cases"]]}
        benchmark = paired_summary(trajectories, options) if trajectories else None
        if benchmark is not None:
            benchmark["by_case"] = {
                name: paired_summary([r for r in trajectories if r["case"] == name], {**options, "cases": [{"id": name}]})
                for name in config["benchmark"]["cases"]
            }
            write_json(output / "trajectories.json", trajectories)
        status = "CALIBRATION-STOP" if calibration["status"] != "passed" else (
            "passed" if all(benchmark["gates"].values()) else "BENCHMARK-STOP"
        )
        summary = dict(stage="P2-B4.1", status=status, noise_scale=fit["noise_scale"],
                       calibration_status=calibration["status"], calibration_gates=calibration["gates"],
                       benchmark=benchmark, benchmark_unique_frames=actual_frames,
                       benchmark_charged_observations=sum(r["observations"] for r in trajectories),
                       new_tracking_attempts=sum(beam.attempts for beam in beams.values()),
                       reused_primary_frames=len(primary), reused_calibration_rows=len(training),
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
    if config["schema"] != "half_infobax_p2_b4_1_weighted_rms_v1" or config["predecessor_config_sha256"] != sha256(INPUT / "config.json"):
        raise ValueError("incorrect weighted calibration or predecessor configuration")
    validate_config({**config, "schema": "half_infobax_p2_b4_rms_calibration_v1"})
    old = json.loads((INPUT / "config.json").read_text())
    if (config["calibration_gates"] != old["calibration_gates"]
            or config["benchmark"]["gates"] != old["benchmark"]["gates"]):
        raise ValueError("weighted continuation must retain the frozen calibration gates")
    for key in ("cases", "workpoints", "scan_values", "particles", "energy_mev", "measurement",
                "primary_particle_seed", "calibration_particle_seeds", "repeat_workpoints", "repeat_scan_values"):
        if config[key] != old[key]:
            raise ValueError(f"reused calibration data do not match configured {key}")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "config.json").write_bytes(raw)
    paths = [Path(__file__), REPO_ROOT / "scripts/calibrate_emit_infobax_rms.py",
             REPO_ROOT / "scripts/diagnose_emit_infobax_measurement.py", REPO_ROOT / "scripts/benchmark_emit_infobax.py",
             REPO_ROOT / "scripts/check_emit_infobax_vm.py", REPO_ROOT / "src/shared/beam_matrix.py",
             REPO_ROOT / "src/shared/elegant_backend/parser.py",
             REPO_ROOT / "src/shared/machine_profile/model_backend.py",
             REPO_ROOT / "src/shared/beam_diagnostics/image_fit.py",
             *sorted((REPO_ROOT / "src/apps/emit_measure").glob("infobax*.py")),
             *sorted((REPO_ROOT / "src/optimization/infobax").glob("*.py"))]
    hashes = {str(p.relative_to(REPO_ROOT)): sha256(p) for stage in ("p2_b2", "p2_b3", "p2_b4")
              for p in sorted((FROZEN / stage).rglob("*")) if p.is_file()}
    write_json(args.output_dir / "metadata.json", dict(
        config_sha256=hashlib.sha256(raw).hexdigest(), source_sha256={str(p.relative_to(REPO_ROOT)): sha256(p) for p in paths},
        predecessor_evidence_sha256=hashes, elegant_binary_sha256=sha256(shutil.which("elegant")), python=platform.python_version(),
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        numpy=np.__version__, scipy=scipy.__version__,
    ))
    run(config, args.output_dir)
    if any(sha256(REPO_ROOT/name) != digest for name, digest in hashes.items()):
        raise RuntimeError("frozen predecessor evidence changed")


if __name__ == "__main__":
    main()
