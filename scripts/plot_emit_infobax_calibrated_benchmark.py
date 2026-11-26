#!/usr/bin/env python3
"""Render paired finite-reference regret and the prespecified aggregate CI."""

import argparse
import json
from pathlib import Path


def plot(root, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    config = json.loads((root / "config.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    rows = json.loads((root / "trajectories.json").read_text())
    indexed = {(r["case"], r["seed"], r["policy"]): r for r in rows}
    figure, axes = plt.subplots(1, 3, figsize=(14, 4.7), constrained_layout=True)
    colors = {"infobax": "#6175a1", "random": "#27826a"}
    for axis, case in zip(axes[:2], config["cases"]):
        counts = []
        for policy in config["policies"]:
            values = [indexed[(case["id"], seed, policy)]["relative_regret"] for seed in config["seeds"]]
            available = np.array([v is not None for v in values])
            x = np.array(config["seeds"])[available]
            y = [v for v in values if v is not None]
            axis.plot(x, y, "o" if policy == "infobax" else "x", color=colors[policy], label=policy, markersize=5)
            counts.append(f"{'I' if policy == 'infobax' else 'R'} {int(sum(available))}/{len(values)}")
        for seed in config["seeds"]:
            pair = [indexed[(case["id"], seed, p)]["relative_regret"] for p in config["policies"]]
            if all(v is not None for v in pair):
                axis.plot([seed, seed], pair, color="#aaaaaa", linewidth=0.6, zorder=0)
        axis.set(xlabel="Paired policy seed", ylabel="Relative regret vs frozen weighted reference",
                 title=f"{case['id']}\nRecommendations: {' | '.join(counts)}")
        axis.grid(alpha=0.2)
        axis.legend(fontsize=8)
    axis = axes[2]
    interval = summary["paired_difference_95_ci"]
    value = summary["paired_mean_relative_regret_difference"]
    axis.axvline(0, color="#9e2222", ls="--", linewidth=1, label="Superiority gate: upper CI < 0")
    if interval is not None:
        axis.hlines(0, interval[0], interval[1], linewidth=3, color="#6175a1")
        axis.plot(value, 0, "o", color="#253e6f")
        axis.set_title(f"Paired mean difference {value:+.4f}\n95% seed CI [{interval[0]:+.4f}, {interval[1]:+.4f}]")
    else:
        axis.text(0.5, 0.5, "No complete paired-seed estimate", transform=axis.transAxes, ha="center")
    axis.set(xlabel="InfoBAX minus Random relative regret", yticks=[])
    axis.grid(axis="x", alpha=0.2)
    axis.legend(fontsize=8)
    completed = "; ".join(f"{p} {summary['policies'][p]['completed_runs']}/{summary['policies'][p]['expected_runs']} completed"
                           for p in config["policies"])
    figure.suptitle(f"P2-B8: {summary['status']} | {completed}\n"
                   f"Complete paired seeds: {summary['complete_paired_seeds']}/{len(config['seeds'])}; "
                   "missing recommendations omitted from regret dots and retained in availability counts", fontsize=10)
    figure.savefig(output, dpi=180)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite an existing comparison figure")
    plot(args.result_dir, args.output)
