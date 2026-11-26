#!/usr/bin/env python3
"""Render a standalone scientific figure from an existing P2-B3 result directory."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def plot(result_dir, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    queries = list(csv.DictReader((result_dir / "queries.csv").open()))
    reconstructions = list(csv.DictReader((result_dir / "reconstructions.csv").open()))
    summary = json.loads((result_dir / "summary.json").read_text())
    config = json.loads((result_dir / "config.json").read_text())
    figure, axes = plt.subplots(1, 3, figsize=(15, 4.6), constrained_layout=True)
    labels = {"particle_rms": "Particle RMS", "pixel_center_rms": "Pixel-center RMS",
              "gaussian_fit": "Gaussian fit"}
    colors = {"particle_rms": "#1967a3", "pixel_center_rms": "#e5a02b", "gaussian_fit": "#c63636"}
    curve = sorted((r for r in queries if r["case"] == "nonlinear" and float(r["workpoint"]) == -2),
                   key=lambda r: float(r["scan_value"]))
    for stream in labels:
        axes[0].plot([float(r["scan_value"]) for r in curve], [float(r[stream]) for r in curve],
                     ".-", label=labels[stream], color=colors[stream])
    axes[0].set(yscale="log", xlabel="Scan K1 [1/m²]", ylabel="Screen variance [mm²]",
                title="A  Same tracked particles, different widths\nStrong sextupole, workpoint K1 = -2")
    axes[0].legend(fontsize=8)
    for stream in labels:
        rows = sorted((r for r in reconstructions if r["case"] == "nonlinear" and r["stream"] == stream),
                      key=lambda r: float(r["workpoint"]))
        axes[1].plot([float(r["workpoint"]) for r in rows],
                     [float(r["emittance_mm_mrad"]) if r["status"] == "valid" else float("nan") for r in rows],
                     ".-", color=colors[stream], label=labels[stream])
        for row in rows:
            if row["status"] != "valid":
                axes[1].plot(float(row["workpoint"]), 0.96, "x", color=colors[stream],
                             transform=axes[1].get_xaxis_transform())
                axes[1].text(float(row["workpoint"]), 0.89, "invalid", ha="center", color=colors[stream],
                             transform=axes[1].get_xaxis_transform(), fontsize=8)
    axes[1].set(xlabel="Workpoint K1 [1/m²]", ylabel="Normalized emittance [mm·mrad]",
                title="B  Shared beam-matrix solver\nInvalid Gaussian reconstructions retained")
    case_colors = {"linear_control": "#7b6ca5", "nonlinear": "#2a8659", "near_tie": "#c67c1f"}
    for case, color in case_colors.items():
        for stream, marker in (("particle_rms", "o"), ("gaussian_fit", "x")):
            rows = [row for row in summary["noise_repeats"]
                    if row["case"] == case and row["stream"] == stream and row["statistics"]]
            axes[2].scatter([r["statistics"]["mean_mm2"] for r in rows],
                            [r["statistics"]["std_mm2"] for r in rows], color=color, marker=marker,
                            alpha=0.8, s=25, label=f"{case}: {labels[stream]}")
    axes[2].axhline(config["noise_std_mm2_under_audit"], color="black", ls="--", lw=1,
                    label="Frozen noise std: 0.002 mm²")
    axes[2].set(xscale="log", yscale="log", xlabel="Mean screen variance [mm²]",
                ylabel="Repeat standard deviation [mm²]",
                title="C  Eight particle populations per point\nSimulation statistics; camera noise unmeasured")
    axes[2].legend(fontsize=6, loc="best")
    for axis in axes:
        axis.grid(alpha=0.2, which="both")
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite an existing figure")
    plot(args.result_dir, args.output)


if __name__ == "__main__":
    main()
