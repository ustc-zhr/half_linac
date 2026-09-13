#!/usr/bin/env python3
"""Offline particle -> pixel image -> scan reconstruction audit. Never uses PVs.

Replays the saved matching K1 baseline with the VM's beam generator in an
isolated directory. The local quad scan uses first-order matrices on those
particles to isolate image/fitting bias from tracking nonlinearities.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from scripts.check_emittance_matching import isolated_model
from half_linac.src.apps.emit_measure.adaptive_scan import restore_scan_quality
from half_linac.src.shared.beam_diagnostics.image_fit import analyze_beam_image
from half_linac.src.shared.elegant_runtime import run_elegant_input
from half_linac.src.shared.machine_profile.model_backend import _load_sdds_columns


def beam_twiss(cov):
    emit = math.sqrt(float(np.linalg.det(cov)))
    return {"beta": float(cov[0, 0] / emit), "alpha": float(-cov[0, 1] / emit), "emittance_m_rad": emit}


def run(scan_dir, matching_path, output, resolutions=(1, 2, 4, 8)):
    output.mkdir(parents=True, exist_ok=True)
    meta = json.loads((scan_dir / "metadata.json").read_text())
    scan = np.loadtxt(scan_dir / "scanResults.txt")
    root = Path(__file__).resolve().parents[1]
    vm_dir = root / "src/virtual_machine/half_elegant"
    vm_bytes = (vm_dir / "halflinac.json").read_bytes()
    state = json.loads(vm_bytes)
    (output / "vm_snapshot.json").write_bytes(vm_bytes)
    if matching_path:
        match = json.loads(matching_path.read_text())["result"]
        baseline = match["request"]["measurement"]
    else:
        baseline = {"point": {"element": meta["quad"], "edge": "entrance"},
                    "energy_mev": meta["energy_mev"],
                    "overrides": {name: {"K1": float(e["K1"])} for name, e in state["lattice"].items() if "K1" in e}}
    source, flag = meta["quad"], meta["flag"]
    if baseline["point"] != {"element": source, "edge": "entrance"}:
        raise ValueError("Audit requires matching measurement at scan quad entrance")
    model = isolated_model(output)
    for name, fields in baseline["overrides"].items():
        state["lattice"][name].update(fields)
    for element in state["lattice"].values():
        if element.get("TYPE", "").upper() == "WATCH":
            element["DISABLE"] = "1"
        for key in ("ZWAKEFILE", "TRWAKEFILE", "WAKEFILE"):
            if key in element:
                element[key] = str((vm_dir / "elegant" / element[key]).resolve())
    state["usedline"] = state["usedline"][:state["usedline"].index(source)] + ["MATCH_INPUT"]
    state["lattice"]["MATCH_INPUT"] = {"NAME": "MATCH_INPUT", "TYPE": "WATCH", "MODE": "coord", "FILENAME": "match_input.out"}
    state_path = output / "particle_input.json"
    state_path.write_text(json.dumps(state))
    parser = model.backend._new_parser()
    parser.json_to_lte_ele(output / "particle_input.lte", output / "particle_input.ele", state_path)
    run_elegant_input("particle_input.ele", "particle_input.log", workdir=output)
    particles = _load_sdds_columns(output / "match_input.out", ("x", "xp", "y", "yp", "p"))
    coordinates = np.array([particles[k] for k in ("x", "xp", "y", "yp")])
    truth = {p: beam_twiss(np.cov(coordinates[i:i+2], bias=True)) for p, i in (("x", 0), ("y", 2))}
    widths = {key: [] for key in ("archived", "particle_rms")}
    for scale in resolutions:
        for method in ("gaussian", "rms", "center_rms", "debiased_rms"):
            widths[f"image_{method}_{scale}x"] = []
    retained = []
    matrices = []
    width, height = meta["image_geometry"]["shape"]
    extent = meta["image_geometry"]["extent_mm"]
    model.backend.energy_mev = baseline["energy_mev"]
    for k1, sx, sy in scan:
        matrix = model.backend.get_map(source, flag, k1=float(k1), lattice_overrides=baseline["overrides"],
                                       seq="ent2exit", twiss_only=True)
        matrices.append(matrix)
        projected = matrix[:4, :4] @ coordinates
        widths["archived"].append([sx, sy])
        widths["particle_rms"].append(np.std(projected[[0, 2]], axis=1) * 1e3)
        for scale in resolutions:
            image, xe, ye = np.histogram2d(projected[0] * 1e3, projected[2] * 1e3,
                bins=(width * scale, height * scale), range=(extent[:2], extent[2:]))
            if scale == resolutions[0]:
                retained.append(float(image.sum() / coordinates.shape[1]))
            _, gaussian = analyze_beam_image(image.T, extent=extent)
            _, moments = analyze_beam_image(image.T, extent=extent, method="RMS moments")
            widths[f"image_gaussian_{scale}x"].append([gaussian.sigx_mm, gaussian.sigy_mm])
            widths[f"image_rms_{scale}x"].append([moments.sigx_mm, moments.sigy_mm])
            variances, corrected = [], []
            for edges, weights in ((xe, image.sum(axis=1)), (ye, image.sum(axis=0))):
                centers = (edges[:-1] + edges[1:]) / 2
                mean = np.average(centers, weights=weights)
                variance = np.average((centers - mean)**2, weights=weights)
                variances.append(np.sqrt(variance))
                corrected.append(np.sqrt(max(0, variance - (edges[1] - edges[0])**2 / 12)))
            widths[f"image_center_rms_{scale}x"].append(variances)
            widths[f"image_debiased_rms_{scale}x"].append(corrected)
    quality = restore_scan_quality(scan[:, 0], meta.get("point_quality", []))
    report = {"scan_dir": str(scan_dir), "matching_archive": str(matching_path), "truth": truth,
              "vm_sha256": hashlib.sha256(vm_bytes).hexdigest(), "baseline": baseline,
              "image_geometry": meta["image_geometry"], "retained_fraction": retained,
              "note": "Full upstream particle tracking; linear local scan isolates image effects. Current VM non-K1 state is not certified historical scan state. Debiased RMS subtracts pixel pitch squared / 12 as a diagnostic approximation only.",
              "reconstruction": {}, "widths_mm": {k: np.asarray(v).tolist() for k, v in widths.items()}}
    for p, i, column in (("x", 0, 0), ("y", 2, 1)):
        mask = np.ones(len(scan), dtype=bool)
        if meta.get("scan_strategy") == "adaptive_quality":
            lower, upper = meta["adaptive_result"][p + "_range"]
            mask = (scan[:, 0] >= lower - .01) & (scan[:, 0] <= upper + .01) & np.array(quality[column])
        A = np.array([[m[i, i]**2, 2*m[i, i]*m[i, i+1], m[i, i+1]**2] for m in matrices])
        report["reconstruction"][p] = {"selected_k1": scan[mask, 0].tolist()}
        for label, values in widths.items():
            sigma2 = np.asarray(values)[:, column]**2 * 1e-6
            moments = np.linalg.lstsq(A[mask], sigma2[mask], rcond=None)[0]
            result = beam_twiss(np.array([[moments[0], moments[1]], [moments[1], moments[2]]]))
            result["relative_emittance_error"] = result["emittance_m_rad"] / truth[p]["emittance_m_rad"] - 1
            report["reconstruction"][p][label] = result
    (output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("truth", "reconstruction")}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-dir", type=Path, required=True)
    parser.add_argument("--matching", type=Path, help="Omit to use a captured current VM K1 snapshot (not certified historical state)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.scan_dir, args.matching, args.output)
