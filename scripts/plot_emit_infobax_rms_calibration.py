#!/usr/bin/env python3
"""Render point variance, reference closure and held-out noise calibration."""

import argparse
import csv
import json
from pathlib import Path


def plot(root, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    primary = list(csv.DictReader((root / "primary.csv").open()))
    references = list(csv.DictReader((root / "references.csv").open()))
    summary = json.loads((root / "calibration_summary.json").read_text())
    config = json.loads((root / "config.json").read_text())
    colors = {"linear_control": "#6175a1", "nonlinear": "#d35d3c", "near_tie": "#27826a"}
    figure, axes = plt.subplots(1, 3, figsize=(14, 4.3), constrained_layout=True)
    for case, color in colors.items():
        rows = [r for r in primary if r["case"] == case and r["status"] == "observed"]
        axes[0].scatter([float(r["particle_rms"]) for r in rows],
                        [100*(float(r["corrected_variance_mm2"])/float(r["particle_rms"])-1) for r in rows],
                        s=15, color=color, label=case, alpha=0.7)
        rows = sorted((r for r in references if r["case"] == case), key=lambda r: float(r["workpoint"]))
        axes[1].plot([float(r["workpoint"]) for r in rows],
                     [100*(float(r["emittance_mm_mrad"])/float(r["entrance_reference_mm_mrad"])-1)
                      if r["status"] == "valid" else float("nan") for r in rows],
                     ".-", color=color, label=case)
        scores = [z for row in summary["noise_validation_points"] if row["case"] == case
                  for z in row["standardized_residuals"]]
        axes[2].hist(scores, bins=np.linspace(-4, 4, 25), histtype="step", density=True,
                     label=case, color=color, linewidth=1.4)
    for axis, bound in ((axes[0], config["calibration_gates"]["maximum_primary_variance_relative_error"]*100),
                        (axes[1], config["calibration_gates"]["maximum_emittance_relative_error"]*100)):
        for sign in (-1, 1):
            axis.axhline(sign*bound, color="#9e2222", ls="--", linewidth=1)
        axis.axhline(0, color="black", linewidth=0.5)
    axes[0].set(xscale="log", xlabel="Particle variance [mm²]", ylabel="Corrected image variance error [%]",
                title="A  Pixel-center RMS minus d²/12\nFrozen variance tolerance: ±2%")
    axes[1].set(xlabel="Workpoint K1 [1/m²]", ylabel="Emittance error vs entrance particles [%]",
                title="B  Full-scan reference closure\nFrozen emittance tolerance: ±5%")
    x = np.linspace(-4, 4, 300)
    axes[2].plot(x, np.exp(-x*x/2)/np.sqrt(2*np.pi), "k--", linewidth=1, label="Unit normal")
    axes[2].set(xlabel="Held-out standardized residual", ylabel="Density",
                title=f"C  Independent validation populations\nMean square: {summary['heldout_standardized_mean_square']:.3f}")
    for axis in axes:
        axis.grid(alpha=0.2)
        axis.legend(fontsize=7)
    figure.savefig(output, dpi=180)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite an existing calibration figure")
    plot(args.result_dir, args.output)
