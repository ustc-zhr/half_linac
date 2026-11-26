#!/usr/bin/env python3
"""Plot B9 posterior physicality and deterministic full-scan target checks."""

import argparse
import csv
import json
from pathlib import Path


def read_csv(path):
    with path.open() as handle:
        return list(csv.DictReader(handle))


def plot(root, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    candidates = read_csv(root / "candidates.csv")
    posteriors = read_csv(root / "posteriors.csv")
    reference = read_csv(root / "reference_audit.csv")
    figure, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    enhanced = [r for r in candidates if r["sample_kind"] == "enhanced"]
    failures = [("nonlinear", 10, "random"), ("near_tie", 12, "infobax")]
    groups = [enhanced]+[[r for r in enhanced if (r["case"], int(r["seed"]), r["policy"]) == key] for key in failures]
    colors = ["#918d98", "#6175a1", "#ac6162", "#d18f52", "#ccbb65", "#27826a"]
    labels = ["Nonfinite curve", "Nonpositive variance", "Nonfinite moments", "Nonpositive diagonal", "Nonpositive determinant", "Physical"]
    bottom = np.zeros(3)
    for key, color, label in zip(("nonfinite_curve", "nonpositive_curve", "nonfinite_moments", "nonpositive_diagonal", "nonpositive_determinant", "physical"), colors, labels):
        fraction = np.array([sum(int(r[key]) for r in group)/(len(group)*16384) for group in groups])*100
        axes[0, 0].bar(np.arange(3), fraction, bottom=bottom, color=color, label=label)
        bottom += fraction
    axes[0, 0].set_xticks(np.arange(3), ["All 64 runs", "Random/nonlinear/10", "InfoBAX/near_tie/12"], fontsize=8)
    axes[0, 0].set(ylabel="Candidate draws [%]", title="A  Exclusive candidate invalidity partition")
    axes[0, 0].set(ylim=(0, 125), yticks=[0, 25, 50, 75, 100])
    axes[0, 0].legend(fontsize=7, ncol=2, loc="upper right")
    axis = axes[0, 1]
    for i, key in enumerate(failures):
        for offset, kind, label, color in ((-0.12, "native", "Original 1024 samples", "#d18f52"),
                                           (0.12, "enhanced", "16384 samples + MC interval", "#6175a1")):
            row = next(r for r in posteriors if (r["case"], int(r["seed"]), r["policy"]) == key
                       and r["observations"] == "14" and r["sample_kind"] == kind)
            value = float(row["invalid_output_probability"])*100
            lo, hi = np.array(json.loads(row["invalid_output_mc_interval"]))*100
            axis.errorbar(i+offset, value, yerr=[[value-lo], [hi-value]], fmt="o", color=color,
                          label=label if i == 0 else None, capsize=3)
    axis.axhline(50, color="#9e2222", ls="--", linewidth=1)
    axis.set_xticks([0, 1], ["Random/nonlinear/10", "InfoBAX/near_tie/12"], fontsize=8)
    axis.set(ylabel="No physical candidate [%]", title="B  Both failures remain above the 50% gate")
    axis.legend(fontsize=8)
    axis = axes[1, 0]
    native = [r for r in candidates if r["sample_kind"] == "native" and r["observations"] == "14"]
    groups, names = [], []
    for case in ("nonlinear", "near_tie"):
        groups.extend([[float(r["reference_curve_off_span_energy"]) for r in reference if r["case"] == case],
                       [float(r["off_span_expected_energy"]) for r in native if r["case"] == case]])
        names.extend([f"Reference\n{case}", f"M0 posterior\n{case}"])
    axis.boxplot(groups, tick_labels=names, showfliers=False)
    axis.set(yscale="log", ylabel="Curve energy outside transport span [fraction]",
             title="C  Full primary curves vs posterior expected energy")
    axis.tick_params(axis="x", labelsize=8)
    axis = axes[1, 1]
    for case, color in (("nonlinear", "#6175a1"), ("near_tie", "#27826a")):
        rows = sorted((r for r in reference if r["case"] == case), key=lambda r: float(r["workpoint"]))
        axis.plot([float(r["workpoint"]) for r in rows],
                  [100*(float(r["m0_emittance_mm_mrad"])/float(r["weighted_fixed_energy_mm_mrad"])-1) for r in rows],
                  ".-", color=color, label=case)
    axis.axhline(0, color="black", linewidth=0.6)
    axis.set(xlabel="Workpoint K1 [1/m²]", ylabel="Unweighted vs weighted emittance [%]",
             title="D  Same complete curves, different full-scan weighting")
    axis.legend(fontsize=8)
    for axis in axes.flat:
        axis.grid(axis="y", alpha=0.2)
        axis.set_axisbelow(True)
    figure.suptitle("P2-B9 posterior diagnosis | B8 BENCHMARK-STOP preserved\n"
                   "No new particle measurements; Monte Carlo intervals condition on fitted posteriors", fontsize=12)
    figure.savefig(output, dpi=180)
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite an existing posterior diagnostic figure")
    plot(args.result_dir, args.output)
