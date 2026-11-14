#!/usr/bin/env python3
"""P2-B5 reference precision factorial; no optimizer, CA or IOC execution."""

from __future__ import annotations

import argparse
import copy
from dataclasses import replace
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
    DiagnosticBeam, covariance_moments, normalized_emittance, csv_rows,
    relative_error, sha256, write_json,
)
from scripts.calibrate_emit_infobax_weighted_rms import read_rows
from half_linac.src.apps.emit_measure.infobax_measurement import RmsVarianceConfig, measure_count_variance
from half_linac.src.shared.beam_matrix import reconstruct_beam_matrix

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_reference_precision.json"
FROZEN = REPO_ROOT / "src/optimization/infobax/benchmarks"


def arm_name(particles, factor):
    return f"{'native' if factor == 1 else 'fine'}_{particles//1000}k"


def resolution_geometry(geometry, factor):
    if type(factor) is not int or factor < 1:
        raise ValueError("resolution factor must be a positive integer")
    return replace(geometry, shape=tuple(value*factor for value in geometry.shape),
                   pixel_width_mm=geometry.pixel_width_mm/factor)


def rasterize_particles(x, y, geometry):
    width, height = geometry.shape
    pitch = geometry.pixel_width_mm
    image, _, _ = np.histogram2d(
        y*1000, x*1000, bins=(height, width),
        range=((-height*pitch/2, height*pitch/2), (-width*pitch/2, width*pitch/2)),
    )
    return image


class PrecisionBeam(DiagnosticBeam):
    def __init__(self, root, config, case, particles, noise_scale):
        super().__init__(root, {**config, "particles": particles}, case)
        self.config = config
        self.particles = particles
        self.rms_config = RmsVarianceConfig(**config["measurement"], noise_scale=noise_scale)

    def measure_pair(self, work, scan, seed):
        from half_linac.src.shared.runtime_state import write_runtime_state
        from half_linac.src.virtual_machine.common.start_VM import _run_elegant
        from half_linac.src.virtual_machine.workbench_data import columns

        self.attempts += 1
        base = dict(case=self.case, workpoint=work, scan_value=scan, particle_seed=seed,
                    particles=self.particles, tracking_attempt=self.attempts, status="failed", error=None)
        try:
            state = copy.deepcopy(self.template)
            state["lattice"]["QT01"]["K1"] = str(work)
            state["lattice"]["QT02"]["K1"] = str(scan)
            state["control"]["run_setup"]["random_number_seed"] = str(seed)
            write_runtime_state(self.root / "input.json", state)
            self.parser.json_to_lte_ele(self.root / "lattice.lte", self.root / "one.ele")
            for name in ("PRE.out", "PRF07.out"):
                (self.root / name).unlink(missing_ok=True)
            _run_elegant(self.root)
            px, xp, p = columns(self.root / "PRE.out", ("x", "xp", "p"))
            x, y = columns(self.root / "PRF07.out", ("x", "y"))
            moments = covariance_moments(px, xp)
            base.update(status="observed", particle_count=len(x), particle_rms=float(np.var(x)*1e6),
                        entrance_moments=moments.tolist(), entrance_beta_gamma=float(p.mean()),
                        entrance_emittance_mm_mrad=normalized_emittance(moments, float(p.mean())))
        except Exception as error:
            base["error"] = f"{type(error).__name__}: {error}"
        output = []
        for factor in self.config["linear_resolution_factors"]:
            row = dict(base, arm=arm_name(self.particles, factor), resolution_factor=factor)
            if base["status"] == "observed":
                try:
                    geometry = resolution_geometry(self.geometry, factor)
                    image = rasterize_particles(x, y, geometry)
                    measured = measure_count_variance(
                        image, pixel_shape=geometry.shape, pixel_width_mm=geometry.pixel_width_mm,
                        config=self.rms_config,
                    )
                    row.update(variance_mm2=measured.variance_mm2, noise_std_mm2=measured.noise_std_mm2,
                               variance_relative_error=relative_error(measured.variance_mm2, base["particle_rms"]),
                               image_retention_fraction=float(image.sum()/len(x)),
                               pixel_shape=list(geometry.shape), pixel_width_mm=geometry.pixel_width_mm,
                               measurement=measured.to_dict())
                except Exception as error:
                    row.update(status="failed", error=f"{type(error).__name__}: {error}")
            output.append(row)
        return output


def reconstruct_arm(rows, projections, config):
    output = []
    for work in config["workpoints"]:
        curve = [next((r for r in rows if r["workpoint"] == work and r["scan_value"] == k), None)
                 for k in config["scan_values"]]
        row = dict(case=rows[0]["case"], arm=rows[0]["arm"], particle_seed=rows[0]["particle_seed"],
                   workpoint=work, status="incomplete_scan", emittance_mm_mrad=None,
                   entrance_reference_mm_mrad=None, relative_error=None, particle_rms_relative_error=None)
        if all(r is not None and r["status"] == "observed" for r in curve):
            variance = np.array([r["variance_mm2"] for r in curve])
            noise = np.array([r["noise_std_mm2"] for r in curve])
            projection = [projections[float(k)] for k in config["scan_values"]]
            result = reconstruct_beam_matrix(projection, np.sqrt(variance)*1e-3,
                                             noise/(2*np.sqrt(variance))*1e-3)
            particle = reconstruct_beam_matrix(projection, np.sqrt([r["particle_rms"] for r in curve])*1e-3)
            reference = float(np.mean([r["entrance_emittance_mm_mrad"] for r in curve]))
            momentum = float(np.mean([r["entrance_beta_gamma"] for r in curve]))
            row.update(status=result.status, message=result.message, solver=result.solver,
                       moments=result.moments, entrance_reference_mm_mrad=reference)
            if result.valid:
                value = result.geometric_emittance_m_rad*momentum*1e6
                row.update(emittance_mm_mrad=value, relative_error=relative_error(value, reference))
            if particle.valid:
                row["particle_rms_relative_error"] = relative_error(particle.geometric_emittance_m_rad*momentum*1e6, reference)
        output.append(row)
    return output


def arm_summary(queries, references, config, seeds):
    output = {}
    expected_frames = len(config["cases"])*len(config["workpoints"])*len(config["scan_values"])*len(seeds)
    expected_refs = len(config["cases"])*len(config["workpoints"])*len(seeds)
    limits = config["reference_gates"]
    for count in config["particle_counts"]:
        for factor in config["linear_resolution_factors"]:
            name = arm_name(count, factor)
            frames = [r for r in queries if r["arm"] == name and r["particle_seed"] in seeds]
            refs = [r for r in references if r["arm"] == name and r["particle_seed"] in seeds]
            maximum_variance = max((r["variance_relative_error"] for r in frames if r["status"] == "observed"), default=None)
            maximum_emittance = max((r["relative_error"] for r in refs if r["status"] == "valid"), default=None)
            maximum_particle = max((r["particle_rms_relative_error"] for r in refs if r["particle_rms_relative_error"] is not None), default=None)
            retention = min((r["image_retention_fraction"] for r in frames if r["status"] == "observed"), default=None)
            gates = dict(
                all_frames_valid=len(frames) == expected_frames and all(r["status"] == "observed" for r in frames),
                all_references_physical=len(refs) == expected_refs and all(r["status"] == "valid" for r in refs),
                variance_closure=maximum_variance is not None and maximum_variance <= limits["maximum_variance_relative_error"],
                emittance_closure=maximum_emittance is not None and maximum_emittance <= limits["maximum_emittance_relative_error"],
                particle_rms_closure=maximum_particle is not None and maximum_particle <= limits["maximum_particle_rms_emittance_relative_error"],
                full_field_retention=retention is not None and retention >= limits["minimum_image_retention_fraction"],
            )
            output[name] = dict(passed=all(gates.values()), gates=gates, frames=len(frames),
                                physical_references=sum(r["status"] == "valid" for r in refs), expected_references=expected_refs,
                                max_variance_relative_error=maximum_variance, max_emittance_relative_error=maximum_emittance,
                                max_particle_rms_emittance_relative_error=maximum_particle,
                                minimum_retention_fraction=retention)
    return output


def choose_primary(primary_summary, order):
    return next((name for name in order if primary_summary[name]["passed"]), None)


def reproduction_check(queries, references, config):
    old_frames = read_rows(FROZEN / "p2_b4_1/primary.csv")
    old_refs = read_rows(FROZEN / "p2_b4_1/references.csv")
    controls = [r for r in queries if r["arm"] == "native_20k" and r["particle_seed"] == config["primary_particle_seed"]]
    control_refs = [r for r in references if r["arm"] == "native_20k" and r["particle_seed"] == config["primary_particle_seed"]]
    if (len(controls) != len(config["cases"])*len(config["workpoints"])*len(config["scan_values"])
            or len(control_refs) != len(config["cases"])*len(config["workpoints"])):
        return dict(passed=False, maximum_relative_error=None, error="incomplete baseline control")
    maximum = 0.0
    for row in queries:
        if row["arm"] != "native_20k" or row["particle_seed"] != config["primary_particle_seed"]:
            continue
        prior = next(r for r in old_frames if (r["case"], r["workpoint"], r["scan_value"]) == (row["case"], row["workpoint"], row["scan_value"]))
        if row["status"] != "observed":
            return dict(passed=False, maximum_relative_error=None, error="failed baseline frame")
        maximum = max(maximum, relative_error(row["variance_mm2"], prior["corrected_variance_mm2"]))
    for row in references:
        if row["arm"] != "native_20k" or row["particle_seed"] != config["primary_particle_seed"]:
            continue
        prior = next(r for r in old_refs if (r["case"], r["workpoint"]) == (row["case"], row["workpoint"]))
        if row["status"] != prior["status"]:
            return dict(passed=False, maximum_relative_error=None, error="baseline reconstruction changed status")
        maximum = max(maximum, relative_error(row["emittance_mm_mrad"], prior["emittance_mm_mrad"]))
    return dict(passed=maximum <= config["reference_gates"]["maximum_native_20k_reproduction_relative_error"],
                maximum_relative_error=maximum)


def run(config, output):
    noise_scale = json.loads((FROZEN / "p2_b4_1/noise_scale_fit.json").read_text())["noise_scale"]
    queries, references, attempts = [], [], 0
    seeds = [config["primary_particle_seed"], *config["validation_particle_seeds"]]
    selected = None
    for seed in seeds:
        for count in config["particle_counts"]:
            for case in config["cases"]:
                with TemporaryDirectory(prefix="infobax-b5-") as temporary:
                    beam = PrecisionBeam(Path(temporary), config, case, count, noise_scale)
                    case_rows = []
                    for work in config["workpoints"]:
                        for scan in config["scan_values"]:
                            case_rows.extend(beam.measure_pair(work, scan, seed))
                        print(f"B5 seed={seed} particles={count} case={case['id']} workpoint={work} recorded", flush=True)
                    queries.extend(case_rows)
                    for factor in config["linear_resolution_factors"]:
                        references.extend(reconstruct_arm([r for r in case_rows if r["resolution_factor"] == factor], beam.projections, config))
                    attempts += beam.attempts
                    csv_rows(output / "queries.csv", queries)
                    csv_rows(output / "references.csv", references)
        if seed == config["primary_particle_seed"]:
            primary = arm_summary(queries, references, config, [seed])
            selected = choose_primary(primary, config["selection_order"])
            write_json(output / "primary_selection.json", dict(selected_arm=selected, primary_arms=primary))
            print(f"B5 primary-only selection frozen: {selected}", flush=True)
    primary = arm_summary(queries, references, config, [config["primary_particle_seed"]])
    heldout = arm_summary(queries, references, config, config["validation_particle_seeds"])
    control = reproduction_check(queries, references, config)
    passed = selected is not None and heldout[selected]["passed"] and control["passed"]
    summary = dict(stage="P2-B5", status="REFERENCE-PASS" if passed else "REFERENCE-PRECISION-STOP",
                   selected_arm=selected, primary_arms=primary, validation_arms=heldout, baseline_reproduction=control,
                   selected_validation_passed=passed, particle_tracking_attempts=attempts, raster_observations=len(queries),
                   failures=[r for r in queries if r["status"] != "observed"],
                   optimizer_runs=0, ioc_access=False,
                   geometry_application="offline rasterization only; no profile/PV/IOC changes",
                   noise_calibration_at_new_settings="pending", gui_acceptance="not established by reference precision alone")
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


def validate_config(config):
    arms = {arm_name(n, f) for n in config["particle_counts"] for f in config["linear_resolution_factors"]}
    if (config["schema"] != "half_infobax_p2_b5_reference_precision_v1"
            or any(type(n) is not int or n < config["measurement"]["minimum_count"] for n in config["particle_counts"])
            or any(type(f) is not int or f < 1 for f in config["linear_resolution_factors"])
            or len(arms) != len(config["particle_counts"])*len(config["linear_resolution_factors"])
            or 20000 not in config["particle_counts"] or 1 not in config["linear_resolution_factors"]
            or config["primary_particle_seed"] in config["validation_particle_seeds"]
            or len(set(config["validation_particle_seeds"])) != len(config["validation_particle_seeds"])
            or not set(config["selection_order"]) <= arms
            or len(set(config["selection_order"])) != len(config["selection_order"])):
        raise ValueError("invalid reference precision configuration or seed leakage")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raw = args.config.read_bytes()
    config = json.loads(raw)
    validate_config(config)
    if config["predecessor_config_sha256"] != sha256(FROZEN / "p2_b4_1/config.json"):
        raise ValueError("predecessor configuration mismatch")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "config.json").write_bytes(raw)
    sources = [Path(__file__), REPO_ROOT / "scripts/check_emit_infobax_vm.py",
               REPO_ROOT / "scripts/diagnose_emit_infobax_measurement.py",
               REPO_ROOT / "scripts/calibrate_emit_infobax_weighted_rms.py",
               REPO_ROOT / "src/apps/emit_measure/infobax_measurement.py",
               REPO_ROOT / "src/shared/beam_matrix.py", REPO_ROOT / "src/shared/elegant_backend/parser.py",
               REPO_ROOT / "src/shared/machine_profile/model_backend.py"]
    frozen = {str(p.relative_to(REPO_ROOT)): sha256(p) for stage in ("p2_b2", "p2_b3", "p2_b4", "p2_b4_1")
              for p in sorted((FROZEN / stage).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    write_json(args.output_dir / "metadata.json", dict(
        config_sha256=sha256(args.config), source_sha256={str(p.relative_to(REPO_ROOT)): sha256(p) for p in sources},
        predecessor_evidence_sha256=frozen, code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
        elegant_binary_sha256=sha256(shutil.which("elegant")),
    ))
    run(config, args.output_dir)
    if any(sha256(REPO_ROOT/name) != digest for name, digest in frozen.items()):
        raise RuntimeError("frozen predecessor evidence changed")


if __name__ == "__main__":
    main()
