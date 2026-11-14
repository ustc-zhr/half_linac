#!/usr/bin/env python3
"""P2-B3 offline width/reference and noise diagnosis; no policy or IOC execution."""

from __future__ import annotations

import argparse
import copy
import csv
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
from scipy.special import ndtr
from scipy.stats import chi2

from half_linac.src.shared.beam_matrix import ELECTRON_REST_ENERGY_MEV, reconstruct_beam_matrix

DEFAULT_CONFIG = REPO_ROOT / "src/optimization/configs/infobax_measurement_diagnostic.json"
PREDECESSOR = REPO_ROOT / "src/optimization/infobax/benchmarks/p2_b2"
STREAMS = ("particle_rms", "pixel_center_rms", "shared_image_rms", "gaussian_fit")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def covariance_moments(x, xp):
    """Population central moments in m², m·rad and rad²."""
    x, xp = np.asarray(x, dtype=float), np.asarray(xp, dtype=float)
    if x.ndim != 1 or x.shape != xp.shape or len(x) < 2 or not np.all(np.isfinite([x, xp])):
        raise ValueError("invalid particle coordinates")
    x, xp = x - x.mean(), xp - xp.mean()
    return np.array([np.mean(x*x), np.mean(x*xp), np.mean(xp*xp)])


def normalized_emittance(moments, beta_gamma):
    a, b, c = moments
    determinant = a*c - b*b
    return float(beta_gamma * np.sqrt(determinant) * 1e6) if a > 0 and c > 0 and determinant > 0 else None


def raster_moments(image, pixel_width_mm):
    image = np.asarray(image, dtype=float)
    if image.ndim != 2 or not np.all(np.isfinite(image)) or np.any(image < 0) or image.sum() <= 0:
        raise ValueError("invalid count image")
    projection = image.sum(axis=0)
    centers = (np.arange(image.shape[1]) + 0.5 - image.shape[1]/2) * pixel_width_mm
    mean = np.sum(centers * projection) / projection.sum()
    return float(np.sum((centers - mean)**2 * projection) / projection.sum())


def relative_error(measured, reference):
    if measured is None or reference is None or reference <= 0:
        return None
    return float(abs(measured / reference - 1))


def repeat_statistics(values, confidence=0.95):
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or len(values) < 2 or not np.all(np.isfinite(values)):
        raise ValueError("at least two finite repeats are required")
    std = float(np.std(values, ddof=1))
    tail, df = (1-confidence)/2, len(values)-1
    return dict(count=len(values), mean_mm2=float(values.mean()), std_mm2=std,
                std_95_ci_mm2=[float(std*np.sqrt(df/chi2.ppf(1-tail, df))),
                               float(std*np.sqrt(df/chi2.ppf(tail, df)))])


class DiagnosticBeam:
    def __init__(self, root, config, case):
        from scripts.check_emit_infobax_vm import prepare_workspace
        from half_linac.src.shared.elegant_backend import ElegantParser
        from half_linac.src.shared.machine_profile import build_model_backend, resolve_element_image_geometry

        self.root = root
        context = prepare_workspace(root, sextupole_k2=case["sextupole_k2"], particles=config["particles"])
        source = root / "source.lte"
        text = source.read_text().replace(
            "SMOKE: LINE=(QT01,D1,S1,QT02,D2,PRF07)",
            'PRE: WATCH,FILENAME="PRE.out",MODE="coord",DISABLE=0\n'
            'SMOKE: LINE=(QT01,D1,S1,PRE,QT02,D2,PRF07)',
        )
        source.write_text(text)
        self.parser = ElegantParser(source, root / "source.ele", "SMOKE", runtime_json_path=root / "input.json")
        self.template = self.parser.build_runtime_state()
        self.geometry = resolve_element_image_geometry(context, "PRF07", "vm")
        backend = build_model_backend(context, energy_mev=config["energy_mev"], line_name="SMOKE")
        self.projections = {
            float(k): tuple(float(v) for v in backend.get_map(
                "QT02", "PRF07", k1=float(k), seq="ent2exit", twiss_only=True,
            )[0, :2]) for k in config["scan_values"]
        }
        self.case = case["id"]
        self.attempts = 0

    def measure(self, workpoint, scan_value, seed):
        from half_linac.src.shared.beam_diagnostics.image_fit import analyze_raw_beam_image, analyze_beam_image
        from half_linac.src.shared.runtime_state import write_runtime_state
        from half_linac.src.virtual_machine.common.start_VM import _run_elegant
        from half_linac.src.virtual_machine.workbench_data import columns

        self.attempts += 1
        row = dict(case=self.case, workpoint=workpoint, scan_value=scan_value, particle_seed=seed,
                   attempt=self.attempts, status="failed", error=None)
        try:
            state = copy.deepcopy(self.template)
            state["lattice"]["QT01"]["K1"] = str(workpoint)
            state["lattice"]["QT02"]["K1"] = str(scan_value)
            state["control"]["run_setup"]["random_number_seed"] = str(seed)
            write_runtime_state(self.root / "input.json", state)
            self.parser.json_to_lte_ele(self.root / "lattice.lte", self.root / "one.ele")
            # Failed simulations must not leave usable outputs from a prior query.
            for name in ("PRE.out", "PRF07.out"):
                (self.root / name).unlink(missing_ok=True)
            _run_elegant(self.root)
            px, pxp, pp = columns(self.root / "PRE.out", ("x", "xp", "p"))
            sx, sxp, sy, sp = columns(self.root / "PRF07.out", ("x", "xp", "y", "p"))
            moments = covariance_moments(px, pxp)
            screen_moments = covariance_moments(sx, sxp)
            beta_gamma = float(pp.mean())
            r11, r12 = self.projections[float(scan_value)]
            predicted = float(np.array([r11*r11, 2*r11*r12, r12*r12]) @ moments * 1e6)
            particle_variance = float(np.var(sx) * 1e6)
            shape = self.geometry.shape
            pixel = self.geometry.pixel_width_mm
            raw = self.parser.load_watch_image(self.root / "PRF07.out", pixel_shape=shape, pixel_width_mm=pixel)
            extent = (-shape[0]*pixel/2, shape[0]*pixel/2, -shape[1]*pixel/2, shape[1]*pixel/2)
            image, gaussian = analyze_raw_beam_image(raw, pixel_shape=shape, extent=extent, flip_y=self.geometry.flip_y)
            _image, rms = analyze_beam_image(image, extent=extent, method="RMS moments")
            skew = float(np.mean((sx-sx.mean())**3) / np.var(sx)**1.5)
            kurtosis = float(np.mean((sx-sx.mean())**4) / np.var(sx)**2 - 3)
            row.update(
                status="observed", particle_count=len(sx), image_retention_fraction=float(image.sum()/len(sx)),
                entrance_moments=moments.tolist(), entrance_beta_gamma=beta_gamma,
                entrance_emittance_mm_mrad=normalized_emittance(moments, beta_gamma),
                entrance_sha256=hashlib.sha256(np.array([px, pxp, pp], dtype="<f8").tobytes()).hexdigest(),
                screen_emittance_mm_mrad=normalized_emittance(screen_moments, float(sp.mean())),
                particle_rms=particle_variance, pixel_center_rms=raster_moments(image, pixel),
                shared_image_rms=float(rms.sigx_mm**2) if rms.valid else None,
                gaussian_fit=float(gaussian.sigx_mm**2) if gaussian.valid else None,
                rms_status=rms.status, gaussian_status=gaussian.status,
                gaussian_message=gaussian.message, prediction_mm2=predicted,
                prediction_relative_error=relative_error(predicted, particle_variance),
                skewness=skew, excess_kurtosis=kurtosis,
                gaussian_residual_rms=gaussian.x_projection.residual_rms,
            )
        except Exception as error:
            row["error"] = f"{type(error).__name__}: {error}"
        return row


def reconstruct(rows, projections, config):
    output = []
    for workpoint in config["workpoints"]:
        curve = [next((r for r in rows if r["workpoint"] == workpoint and r["scan_value"] == scan), None)
                 for scan in config["scan_values"]]
        entrance = [r["entrance_emittance_mm_mrad"] for r in curve if r is not None and r["status"] == "observed"]
        reference = float(np.mean(entrance)) if entrance and all(v is not None for v in entrance) else None
        for stream in STREAMS:
            row = dict(case=rows[0]["case"], workpoint=workpoint, stream=stream,
                       entrance_reference_mm_mrad=reference, emittance_mm_mrad=None, relative_error=None)
            if any(r is None or r.get(stream) is None or r.get(stream, 0) <= 0 for r in curve):
                row.update(status="incomplete_scan", error="missing or nonpositive width")
            else:
                result = reconstruct_beam_matrix(
                    [projections[float(k)] for k in config["scan_values"]],
                    np.sqrt([r[stream] for r in curve]) * 1e-3,
                )
                row.update(status=result.status, error=result.message,
                           moments=result.moments, condition_number=result.condition_number)
                if result.valid:
                    # Use the same physical particle momentum for every width stream.
                    momentum = float(np.mean([r["entrance_beta_gamma"] for r in curve]))
                    emittance = result.geometric_emittance_m_rad * momentum * 1e6
                    row.update(emittance_mm_mrad=emittance, relative_error=relative_error(emittance, reference))
            output.append(row)
    return output


def max_or_none(values):
    finite = [float(value) for value in values if value is not None and np.isfinite(value)]
    return max(finite) if finite else None


def gate_below(value, bound):
    return value is not None and value <= bound


def summarize(primary, references, repeats, deterministic, config):
    observed = [row for row in primary if row["status"] == "observed"]
    expected = len(config["cases"]) * len(config["workpoints"]) * len(config["scan_values"])
    covariance_drift = []
    for case in config["cases"]:
        for workpoint in config["workpoints"]:
            group = [r for r in observed if r["case"] == case["id"] and r["workpoint"] == workpoint]
            if group:
                base = np.asarray(group[0]["entrance_moments"])
                covariance_drift.extend(float(np.linalg.norm(np.asarray(r["entrance_moments"])-base)/np.linalg.norm(base)) for r in group)
    streams = {}
    for stream in STREAMS:
        reconstructed = [row for row in references if row["stream"] == stream]
        streams[stream] = dict(
            physical_reconstructions=sum(row["status"] == "valid" for row in reconstructed),
            total_reconstructions=len(config["cases"]) * len(config["workpoints"]),
            max_variance_relative_error=max_or_none(relative_error(row.get(stream), row["particle_rms"]) for row in observed),
            max_emittance_relative_error=max_or_none(row["relative_error"] for row in reconstructed),
        )
    noise_rows = []
    for case in config["cases"]:
        for work in config["repeat_workpoints"]:
            for scan in config["repeat_scan_values"]:
                group = [r for r in repeats if r["case"] == case["id"] and r["workpoint"] == work and r["scan_value"] == scan]
                for stream in ("particle_rms", "gaussian_fit"):
                    values = [r[stream] for r in group if r.get(stream) is not None]
                    stats = repeat_statistics(values, config["gates"]["repeat_standard_deviation_confidence_level"]) if len(values) >= 2 else None
                    noise_rows.append(dict(case=case["id"], workpoint=work, scan_value=scan,
                                           stream=stream, statistics=stats,
                                           missing_repeats=len(config["repeat_particle_seeds"])-len(values)))
    noise = config["noise_std_mm2_under_audit"]
    probabilities = [float(ndtr(-r["gaussian_fit"]/noise)) for r in observed if r.get("gaussian_fit") is not None]
    maximum_probability = max_or_none(probabilities)
    limits = config["gates"]
    all_queries_valid = len(observed) == expected and all(row.get("gaussian_fit") is not None and row.get("shared_image_rms") is not None for row in observed)
    expected_references = len(config["cases"]) * len(config["workpoints"])
    prediction_error = max_or_none(r["prediction_relative_error"] for r in observed)
    max_drift = max_or_none(covariance_drift)
    gates = dict(
        all_primary_queries_and_fits_valid=all_queries_valid,
        fixed_entrance_covariance=gate_below(max_drift, limits["maximum_entrance_covariance_relative_drift"]),
        linear_map_predicts_particle_variances=gate_below(prediction_error, limits["maximum_linear_prediction_variance_relative_error"]),
        particle_rms_reference_physical=streams["particle_rms"]["physical_reconstructions"] == expected_references,
        particle_rms_emittance_closure=gate_below(streams["particle_rms"]["max_emittance_relative_error"], limits["maximum_particle_rms_emittance_relative_error"]),
        pixel_center_rms_width_closure=gate_below(streams["pixel_center_rms"]["max_variance_relative_error"], limits["maximum_raster_variance_relative_error"]),
        shared_image_rms_width_closure=gate_below(streams["shared_image_rms"]["max_variance_relative_error"], limits["maximum_raster_variance_relative_error"]),
        gaussian_width_closure=gate_below(streams["gaussian_fit"]["max_variance_relative_error"], limits["maximum_gaussian_variance_relative_error"]),
        gaussian_reference_physical=streams["gaussian_fit"]["physical_reconstructions"] == expected_references,
        gaussian_emittance_closure=gate_below(streams["gaussian_fit"]["max_emittance_relative_error"], limits["maximum_gaussian_emittance_relative_error"]),
        configured_noise_nonpositive_risk=gate_below(maximum_probability, limits["maximum_configured_noise_nonpositive_probability"]),
        all_simulated_repeats_valid=all(row["missing_repeats"] == 0 for row in noise_rows),
        deterministic_repeats_match=all(r["matched"] for r in deterministic) and len(deterministic) == len(config["cases"]),
    )
    return dict(stage="P2-B3", status="MEASUREMENT-NOISE-STOP" if not all(gates.values()) else "diagnostic_gates_passed",
                primary_queries=expected, observed_queries=len(observed), primary_failures=[r for r in primary if r["status"] != "observed"],
                streams=streams, max_entrance_covariance_relative_drift=max_drift,
                max_linear_prediction_variance_relative_error=prediction_error,
                max_nonpositive_probability=maximum_probability,
                gaussian_noise_has_strictly_positive_support=False,
                repeat_interpretation=config["repeat_interpretation"],
                noise_repeats=noise_rows, deterministic_repeats=deterministic, gates=gates,
                optimizer_runs=0, ioc_access=False, real_machine_noise_calibration="pending",
                gui_acceptance="blocked" if not all(gates.values()) else "requires a newly frozen benchmark")


def csv_rows(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, allow_nan=False) if isinstance(value, (dict, list, tuple)) else value
                             for key, value in row.items()})


def run(config, output):
    primary, repeats, references, deterministic = [], [], [], []
    for case in config["cases"]:
        print(f"Primary diagnostic: {case['id']}", flush=True)
        case_rows, cache = [], {}
        with TemporaryDirectory(prefix="infobax-b3-") as temporary:
            beam = DiagnosticBeam(Path(temporary), config, case)
            for work in config["workpoints"]:
                for scan in config["scan_values"]:
                    row = beam.measure(work, scan, config["primary_particle_seed"])
                    case_rows.append(row)
                    cache[(work, scan, config["primary_particle_seed"])] = row
                print(f"case={case['id']} workpoint={work} full scan recorded", flush=True)
            primary.extend(case_rows)
            references.extend(reconstruct(case_rows, beam.projections, config))
            csv_rows(output / "queries.csv", primary)
            csv_rows(output / "reconstructions.csv", references)
            print(f"Particle-population repeats: {case['id']}", flush=True)
            for work in config["repeat_workpoints"]:
                for scan in config["repeat_scan_values"]:
                    for seed in config["repeat_particle_seeds"]:
                        row = cache.get((work, scan, seed))
                        if row is None:
                            row = beam.measure(work, scan, seed)
                            cache[(work, scan, seed)] = row
                        repeats.append(row)
            point = config["deterministic_rerun_point"]
            old = cache[(point["workpoint"], point["scan_value"], point["seed"])]
            new = beam.measure(point["workpoint"], point["scan_value"], point["seed"])
            errors = [relative_error(new.get(stream), old.get(stream)) for stream in STREAMS]
            matched = (old["status"] == new["status"] == "observed"
                       and old["entrance_sha256"] == new["entrance_sha256"]
                       and all(gate_below(error, config["gates"]["maximum_deterministic_repeat_relative_error"]) for error in errors))
            deterministic.append(dict(case=case["id"], point=point, matched=matched,
                                      stream_relative_errors=errors, rerun=new, tracking_attempts=beam.attempts))
            csv_rows(output / "repeats.csv", repeats)
            write_json(output / "deterministic_repeats.json", deterministic)
    summary = summarize(primary, references, repeats, deterministic, config)
    # Reproduce the two frozen B2 nonphysical references without replacing them.
    frozen = json.loads((PREDECESSOR / "references.json").read_text())
    summary["predecessor_reference_statuses_reproduced"] = all(
        [r["status"] for r in references if r["case"] == case and r["stream"] == "gaussian_fit"]
        == frozen[case]["reconstruction_statuses"] for case in frozen
    )
    summary["tracking_attempts"] = sum(r["tracking_attempts"] for r in deterministic)
    write_json(output / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key not in ("noise_repeats", "deterministic_repeats")}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raw = args.config.read_bytes()
    config = json.loads(raw)
    if (config["schema"] != "half_infobax_p2_b3_measurement_diagnostic_v1"
            or tuple(config["streams"]) != STREAMS
            or config["predecessor_config_sha256"] != sha256(PREDECESSOR / "config.json")
            or config["primary_particle_seed"] not in config["repeat_particle_seeds"]
            or len(config["repeat_particle_seeds"]) != len(set(config["repeat_particle_seeds"]))):
        raise ValueError("incompatible diagnostic configuration")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "config.json").write_bytes(raw)
    paths = [Path(__file__), REPO_ROOT / "scripts/check_emit_infobax_vm.py",
             REPO_ROOT / "src/shared/beam_matrix.py", REPO_ROOT / "src/shared/beam_diagnostics/image_fit.py",
             REPO_ROOT / "src/shared/elegant_backend/parser.py", REPO_ROOT / "src/shared/machine_profile/model_backend.py",
             REPO_ROOT / "src/virtual_machine/workbench_data.py"]
    old_hashes = {str(p.relative_to(REPO_ROOT)): sha256(p) for p in sorted(PREDECESSOR.glob("*")) if p.is_file()}
    metadata = dict(config_sha256=hashlib.sha256(raw).hexdigest(),
                    source_sha256={str(p.relative_to(REPO_ROOT)): sha256(p) for p in paths},
                    predecessor_evidence_sha256=old_hashes,
                    code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
                    elegant_binary_sha256=sha256(shutil.which("elegant")),
                    python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
                    ioc_access=False, optimizer_runs=0)
    write_json(args.output_dir / "metadata.json", metadata)
    run(config, args.output_dir)
    if old_hashes != {str(p.relative_to(REPO_ROOT)): sha256(p) for p in sorted(PREDECESSOR.glob("*")) if p.is_file()}:
        raise RuntimeError("frozen P2-B2 evidence changed")


if __name__ == "__main__":
    main()
