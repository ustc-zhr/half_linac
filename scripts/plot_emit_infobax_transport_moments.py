#!/usr/bin/env python3
"""Render fixed-history physicality and withheld-prediction comparisons."""

import argparse
import json
from pathlib import Path


def plot(root, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    summary = json.loads((root / "summary.json").read_text())
    config = json.loads((root / "config.json").read_text())
    counts = config["observation_counts"]
    figure, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    colors = {"m0": "#6175a1", "transport_moments": "#27826a"}
    names = {"m0": "M0 response GP", "transport_moments": "Transport moment GP"}
    positions = np.arange(len(counts))
    for axis, metric, factor, title, ylabel in (
        (axes[0, 0], "mean_invalid_probability", 100, "A  Invalid-output mass", "Mean probability [%]"),
        (axes[0, 1], "mean_withheld_normalized_rmse", 1, "B  Withheld finite-primary response prediction", "Mean normalized RMSE"),
    ):
        for offset, model in ((-0.19, "m0"), (0.19, "transport_moments")):
            values = [summary["by_observation_count"][str(n)][model][metric]*factor for n in counts]
            bars = axis.bar(positions+offset, values, width=0.36, color=colors[model], label=names[model])
            axis.bar_label(bars, fmt="%.3f", fontsize=8, padding=3)
        axis.set_xticks(positions, counts)
        axis.set(xlabel="Frozen observations per history", ylabel=ylabel, title=title)
        axis.set_ylim(0, axis.get_ylim()[1]*1.15)
        axis.legend(fontsize=8)
    for axis, metric, title, xlabel in (
        (axes[1, 0], "withheld_normalized_rmse", "C  Paired prediction-error difference", "Moment GP minus M0 normalized RMSE"),
        (axes[1, 1], "withheld_nlpd", "D  Paired predictive-density difference", "Moment GP minus M0 negative log density"),
    ):
        for i, count in enumerate(counts):
            row = summary["paired_differences"][str(count)][metric]
            value, (lo, hi) = row["mean_transport_minus_m0"], row["paired_seed_95_interval"]
            axis.hlines(i, lo, hi, color="#6175a1", linewidth=3)
            axis.plot(value, i, "o", color="#253e6f")
            axis.annotate(f"{value:+.3f} [{lo:+.3f}, {hi:+.3f}]", (value, i), xytext=(0, 13),
                          textcoords="offset points", ha="center", fontsize=8)
        axis.axvline(0, color="#9e2222", ls="--", linewidth=1)
        axis.margins(x=0.25)
        axis.set_yticks(range(len(counts)), [f"{n} observations" for n in counts])
        axis.set(title=title, xlabel=xlabel, ylim=(-0.5, len(counts)-0.3))
    for axis in axes.flat:
        axis.grid(alpha=0.2)
        axis.set_axisbelow(True)
    figure.suptitle("P2-B10: fixed-history posterior comparison | No new policy or machine run\n"
                   "95% intervals resample 16 seeds, keeping both cases and historical policies together", fontsize=11)
    figure.savefig(output, dpi=180)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite an existing moment-comparison figure")
    plot(args.result_dir, args.output)
