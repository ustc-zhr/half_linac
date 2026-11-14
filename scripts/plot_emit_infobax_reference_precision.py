#!/usr/bin/env python3
"""Plot frozen primary and held-out maxima for the P2-B5 factorial."""

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
    arms = list(summary["primary_arms"])
    selected = summary["selected_arm"]
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.8), constrained_layout=True)
    x = np.arange(len(arms))
    for axis, metric, gate, title in (
        (axes[0], "max_variance_relative_error", "maximum_variance_relative_error", "Image variance vs particle variance"),
        (axes[1], "max_emittance_relative_error", "maximum_emittance_relative_error", "Weighted reference vs entrance emittance"),
    ):
        for offset, key, label, color in (
            (-0.19, "primary_arms", "Primary seed (selection)", "#6175a1"),
            (0.19, "validation_arms", "Two new seeds (validation)", "#27826a"),
        ):
            values = [summary[key][arm][metric] for arm in arms]
            bars = axis.bar(x+offset, [100*v if v is not None else np.nan for v in values],
                            width=0.36, color=color, label=label)
            axis.bar_label(bars, fmt="%.2f", fontsize=8, padding=3)
        bound = 100*config["reference_gates"][gate]
        axis.axhline(bound, color="#9e2222", ls="--", linewidth=1, label=f"Frozen limit: {bound:g}%")
        axis.set_xticks(x, [arm.replace("_", "\n")+("\nselected" if arm == selected else "") for arm in arms])
        axis.set(ylabel="Maximum absolute relative error [%]", title=title)
        axis.set_ylim(0, axis.get_ylim()[1]*1.15)
        axis.grid(axis="y", alpha=0.2)
        axis.set_axisbelow(True)
        axis.legend(fontsize=8)
    failures = len(summary["failures"])
    figure.suptitle(f"P2-B5: {summary['status']} | Failed frames: {failures}\n"
                   "Same field of view; paired raster resolutions; noise recalibration pending", fontsize=11)
    figure.savefig(output, dpi=180)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite an existing precision figure")
    plot(args.result_dir, args.output)
