#!/usr/bin/env python3
"""Plot whole-seed validation residuals and conditional bootstrap intervals."""

import argparse
import csv
import json
from pathlib import Path


def plot(root, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    config = json.loads((root / "config.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    uncertainty = json.loads((root / "seed_uncertainty.json").read_text())
    fit = json.loads((root / "noise_scale_fit.json").read_text())
    with (root / "validation_scores.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    indexed = {(int(r["particle_seed"]), r["case"], float(r["workpoint"]), float(r["scan_value"])):
               float(r["standardized_residual"]) for r in rows}
    points = [(case["id"], w, s) for case in config["cases"]
              for w in config["repeat_workpoints"] for s in config["repeat_scan_values"]]
    z = np.array([[indexed[(seed, *point)] for point in points] for seed in config["validation_particle_seeds"]])
    figure = plt.figure(figsize=(12, 7), constrained_layout=True)
    grid = figure.add_gridspec(3, 2, width_ratios=[1.6, 1])
    heat = figure.add_subplot(grid[:, 0])
    limit = max(2, float(np.ceil(np.max(np.abs(z)))))
    artist = heat.imshow(z, cmap="RdBu_r", vmin=-limit, vmax=limit, aspect="auto", interpolation="nearest")
    per_case = len(config["repeat_workpoints"])*len(config["repeat_scan_values"])
    heat.set_xticks([per_case*(i+0.5)-0.5 for i in range(len(config["cases"]))],
                    [case["id"] for case in config["cases"]], fontsize=9)
    for boundary in range(per_case, len(points), per_case):
        heat.axvline(boundary-0.5, color="black", linewidth=0.7)
    heat.set(xlabel="Optical points grouped by case", ylabel="Validation seed index (CSV records seed IDs)",
             title="Same seed retained across every optical point")
    heat.set_yticks(np.arange(0, len(z), 4), np.arange(1, len(z)+1, 4))
    figure.colorbar(artist, ax=heat, label="Standardized residual z", shrink=0.65)
    specs = [("coverage", 100, [90, 100], "Empirical 95% interval coverage [%]"),
             ("standardized_mean", 1, [-0.5, 0.5], "Standardized mean"),
             ("standardized_mean_square", 1, [0.5, 2], "Standardized mean square")]
    for i, (key, factor, allowed, label) in enumerate(specs):
        axis = figure.add_subplot(grid[i, 1])
        result = uncertainty[key]
        value = result["estimate"]*factor
        lo, hi = np.array(result["seed_bootstrap_interval"])*factor
        axis.axvspan(*allowed, alpha=0.12, color="#27826a", label="Frozen gate for point estimate")
        axis.hlines(0, lo, hi, color="#6175a1", linewidth=3)
        axis.plot(value, 0, "o", color="#253e6f")
        axis.set(yticks=[], xlabel=label, title=f"{value:.3f}; 95% seed interval [{lo:.3f}, {hi:.3f}]")
        axis.grid(axis="x", alpha=0.2)
        axis.legend(fontsize=7, loc="upper left")
    a, b = fit["seed_bootstrap"]["noise_scale_interval"]
    figure.suptitle(f"P2-B7: {summary['status']} | Training scale {fit['noise_scale']:.4f} [{a:.4f}, {b:.4f}]\n"
                   "Validation intervals condition on the frozen training fit; descriptive, no new pass criterion", fontsize=11)
    figure.savefig(output, dpi=180)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite an existing uncertainty figure")
    plot(args.result_dir, args.output)
