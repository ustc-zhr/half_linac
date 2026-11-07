#!/usr/bin/env python3
"""Validate causal energy prediction from saved jitter timed acquisitions.

This command is offline and read-only with respect to the accelerator.  It
trains compact baseline models and writes an audit report; it does not export a
control model or connect to EPICS.

Example:
    source scripts/setup.sh
    python3 scripts/validate_energy_prediction.py runs/energy_prediction
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PARENT = REPO_ROOT.parent
if str(REPO_PARENT) not in sys.path:
    sys.path.insert(0, str(REPO_PARENT))

from half_linac.src.shared.energy_prediction_validation import (  # noqa: E402
    DEFAULT_HORIZONS_SECONDS,
    DEFAULT_LAGS_SECONDS,
    DEFAULT_SEASONAL_PERIOD_SECONDS,
    DEFAULT_TARGET_ID,
    audit_run,
    build_examples,
    chronological_split,
    combine_examples,
    default_feature_groups,
    discover_run_directories,
    finite_json,
    fit_and_score_models,
    load_timed_run,
)


def _number_list(value: str, label: str) -> tuple[float, ...]:
    try:
        numbers = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be comma-separated numbers") from exc
    if not numbers:
        raise argparse.ArgumentTypeError(f"{label} cannot be empty")
    return numbers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_paths",
        nargs="+",
        type=Path,
        help="raw.h5, run directory, or root containing jitter run directories",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: first input/energy_prediction_analysis)",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET_ID,
        help=f"Energy target object ID (default: {DEFAULT_TARGET_ID})",
    )
    parser.add_argument(
        "--horizons",
        default=",".join(f"{value:g}" for value in DEFAULT_HORIZONS_SECONDS),
        help="Prediction horizons in seconds, comma-separated (default: 1,2,5,10)",
    )
    parser.add_argument(
        "--lags",
        default=",".join(f"{value:g}" for value in DEFAULT_LAGS_SECONDS),
        help="Causal history lags in seconds, comma-separated (default: 0,1,2,5,10,30)",
    )
    parser.add_argument(
        "--feature",
        action="append",
        default=[],
        help="Use a custom feature ID; repeat for multiple features",
    )
    parser.add_argument(
        "--seasonal-period",
        type=float,
        default=DEFAULT_SEASONAL_PERIOD_SECONDS,
        help="Seasonal persistence period in seconds; 0 disables it (default: 9)",
    )
    parser.add_argument(
        "--test-run",
        help="Run ID reserved for testing (default: chronologically last run)",
    )
    parser.add_argument(
        "--fct-threshold",
        type=float,
        default=0.2,
        help="FCT beam-present threshold (default: 0.2)",
    )
    parser.add_argument(
        "--ict-threshold",
        type=float,
        default=0.1,
        help="ICT01 beam-present threshold (default: 0.1)",
    )
    parser.add_argument(
        "--skip-mlp",
        action="store_true",
        help="Skip the small neural-network candidate",
    )
    parser.add_argument("--random-state", type=int, default=17)
    args = parser.parse_args()
    args.horizons = _number_list(args.horizons, "horizons")
    args.lags = _number_list(args.lags, "lags")
    if any(value <= 0 for value in args.horizons):
        parser.error("all horizons must be positive")
    if any(value < 0 for value in args.lags):
        parser.error("all lags must be non-negative")
    if args.seasonal_period < 0:
        parser.error("seasonal period cannot be negative")
    return args


def _default_output_path(first_input: Path) -> Path:
    path = first_input.expanduser().resolve()
    if path.is_file():
        path = path.parent
    return path / "energy_prediction_analysis"


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(finite_json(rows))


def _format(value, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "n/a"
    return f"{number:.{digits}g}"


def _write_report(
    path: Path,
    *,
    target_id: str,
    lags: tuple[float, ...],
    seasonal_period: float,
    audits: list[dict[str, object]],
    groups: dict[str, tuple[str, ...]],
    missing_features: dict[str, tuple[str, ...]],
    split_description: str,
    metrics: list[dict[str, object]],
    skipped: list[dict[str, object]],
) -> None:
    lines = [
        "# HALF 能量前馈预测离线验证",
        "",
        "本报告由 `scripts/validate_energy_prediction.py` 生成。分析过程只读取 jitter HDF5，",
        "不连接 EPICS、不写控制 PV，也不导出可供反馈直接执行的模型。",
        "",
        "## 任务定义",
        "",
        f"- 预测目标：`{target_id}` 的未来值",
        f"- 历史滞后：{', '.join(f'{item:g} s' for item in lags)}",
        f"- 周期保持基线：{seasonal_period:g} s",
        f"- 数据划分：{split_description}",
        "- 相位特征使用 sin/cos 编码，避免 -180°/180° 跳变",
        "- persistence 使用当前能量；seasonal persistence 使用目标时刻前一周期的能量",
        "- `skill vs best baseline > 0` 才表示优于全部简单基线",
        "",
        "## 数据与时间完整性",
        "",
        "| run | 点数 | 时长 (s) | 中位间隔 (s) | P95 间隔 (s) | 最大间隔 (s) | 目标有效率 | 相邻不变率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in audits:
        lines.append(
            f"| {row['run_id']} | {row['samples']} | {_format(row['duration_seconds'])} | "
            f"{_format(row['cadence_median_seconds'])} | {_format(row['cadence_p95_seconds'])} | "
            f"{_format(row['maximum_gap_seconds'])} | "
            f"{_format(100.0 * float(row['target_valid_fraction']))}% | "
            f"{_format(100.0 * float(row['target_unchanged_pair_fraction']))}% |"
        )

    lines.extend(["", "## 特征组", ""])
    for name, feature_ids in groups.items():
        lines.append(f"- `{name}`：{', '.join(f'`{item}`' for item in feature_ids)}")
        missing = missing_features.get(name, ())
        if missing:
            lines.append(
                "  缺失并自动排除：" + ", ".join(f"`{item}`" for item in missing)
            )

    lines.extend(
        [
            "",
            "## 测试结果",
            "",
            "| 特征组 | 提前量 (s) | 模型 | 训练/测试点 | MAE (MeV) | RMSE (MeV) | skill vs persistence | skill vs best baseline | 方向准确率 |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in metrics:
        lines.append(
            f"| {row['group']} | {_format(row['horizon_seconds'])} | {row['model']} | "
            f"{row['train_examples']}/{row['test_examples']} | {_format(row['mae_mev'])} | "
            f"{_format(row['rmse_mev'])} | {_format(row['skill_vs_persistence'])} | "
            f"{_format(row['skill_vs_best_baseline'])} | "
            f"{_format(100.0 * float(row['direction_accuracy']))}% |"
        )
    if not metrics:
        lines.append("| - | - | 没有形成可训练的数据组合 | - | - | - | - | - | - |")

    if skipped:
        lines.extend(["", "## 跳过的组合", ""])
        for row in skipped:
            lines.append(
                f"- `{row['group']}` / {row['horizon_seconds']:g} s：{row['reason']}"
            )

    positive = [
        row
        for row in metrics
        if row["model"] not in {
            "persistence",
            "history_mean",
            "seasonal_persistence",
        }
        and float(row["skill_vs_best_baseline"]) > 0
    ]
    lines.extend(["", "## 当前结论", ""])
    if positive:
        best = max(positive, key=lambda row: float(row["skill_vs_persistence"]))
        lines.append(
            f"当前测试中最佳候选为 `{best['model']}`（`{best['group']}`，"
            f"{float(best['horizon_seconds']):g} s），相对最佳简单基线的 skill 为 "
            f"{float(best['skill_vs_best_baseline']):.3f}。这只是离线候选；还需要跨日期复现和"
            "能量反馈影子模式验证。"
        )
    else:
        lines.append(
            "当前没有学习模型稳定优于 persistence。优先检查信号更新率、时间对齐、"
            "数据量和工况覆盖，不应直接扩大网络规模。"
        )
    lines.extend(
        [
            "",
            "## 判读限制",
            "",
            "- jitter HDF5 当前保存批次采集时间，而不是每个 PV 的设备源时间戳；本结果首先用于秒级慢漂移验证。",
            "- 一次运行的前后段划分只能用于早期试验；正式结论需要整次 run、最好跨日期留出测试。",
            "- 高相邻不变率可能表示 PV 更新慢于轮询，重复读取不能视为独立样本。",
            "- 离线预测误差改善不等于闭环改善；下一阶段只能先进入只读影子预测。",
            "",
            "## 产物",
            "",
            "- `run_audit.csv`：采样、时间间隔和目标更新检查",
            "- `metrics.csv`：各特征组、提前量和模型的测试指标",
            "- `predictions.csv`：测试集逐点预测，便于画图和残差复查",
            "- `summary.json`：完整机器可读结果",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    run_directories = discover_run_directories(args.run_paths)
    runs = sorted(
        (load_timed_run(path) for path in run_directories),
        key=lambda run: float(run.timestamps[0]),
    )
    if len({run.run_id for run in runs}) != len(runs):
        raise SystemExit("Run IDs must be unique for train/test separation")
    if any(args.target not in run.index for run in runs):
        missing = [run.run_id for run in runs if args.target not in run.index]
        raise SystemExit(f"Target {args.target!r} is missing in runs: {missing}")

    if args.feature:
        feature_ids = tuple(dict.fromkeys((args.target, *args.feature)))
        common = set.intersection(*(set(run.object_ids) for run in runs))
        missing = tuple(item for item in feature_ids if item not in common)
        if missing:
            raise SystemExit(f"Custom features are not common to all runs: {missing}")
        groups = {"custom": feature_ids}
        missing_features = {"custom": ()}
    else:
        groups, missing_features = default_feature_groups(runs, args.target)

    audits = [audit_run(run, args.target) for run in runs]
    if len(runs) > 1:
        test_run_id = args.test_run or runs[-1].run_id
        if test_run_id not in {run.run_id for run in runs}:
            raise SystemExit(f"Unknown --test-run {test_run_id!r}")
        train_runs = [run for run in runs if run.run_id != test_run_id]
        test_runs = [run for run in runs if run.run_id == test_run_id]
        split_description = (
            f"整次 run 留出；训练={','.join(run.run_id for run in train_runs)}，"
            f"测试={test_run_id}"
        )
    else:
        train_runs = runs
        test_runs = runs
        split_description = (
            "仅有一次 run，使用前 70%/后 30% 并留出历史窗口与预测提前量的 purge gap；"
            "该结果只视为初筛"
        )

    metrics: list[dict[str, object]] = []
    prediction_rows: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    example_counts: list[dict[str, object]] = []
    for group_name, feature_ids in groups.items():
        if args.target not in feature_ids:
            continue
        for horizon in args.horizons:
            try:
                by_run = {
                    run.run_id: build_examples(
                        run,
                        group=group_name,
                        feature_ids=feature_ids,
                        target_id=args.target,
                        lags_seconds=args.lags,
                        horizon_seconds=horizon,
                        seasonal_period_seconds=args.seasonal_period,
                        fct_threshold=args.fct_threshold,
                        ict_threshold=args.ict_threshold,
                    )
                    for run in runs
                }
                for run_id, examples in by_run.items():
                    example_counts.append(
                        {
                            "run_id": run_id,
                            "group": group_name,
                            "horizon_seconds": horizon,
                            "examples": len(examples),
                        }
                    )
                if len(runs) > 1:
                    train = combine_examples(
                        [by_run[run.run_id] for run in train_runs],
                        "+".join(run.run_id for run in train_runs),
                    )
                    test = combine_examples(
                        [by_run[run.run_id] for run in test_runs],
                        "+".join(run.run_id for run in test_runs),
                    )
                else:
                    all_examples = by_run[runs[0].run_id]
                    train, test = chronological_split(
                        all_examples,
                        purge_seconds=max(args.lags) + float(horizon),
                    )
                result_rows, predictions = fit_and_score_models(
                    train,
                    test,
                    include_mlp=not args.skip_mlp,
                    random_state=args.random_state,
                )
                metrics.extend(result_rows)
                for model_name, predicted in predictions.items():
                    for index, value in enumerate(predicted):
                        prediction_rows.append(
                            {
                                "group": group_name,
                                "horizon_seconds": horizon,
                                "model": model_name,
                                "test_run": test.run_id,
                                "timestamp": datetime.fromtimestamp(
                                    float(test.timestamps[index])
                                ).isoformat(timespec="milliseconds"),
                                "actual_energy_mev": float(test.targets[index]),
                                "current_energy_mev": float(test.current_energy[index]),
                                "predicted_energy_mev": float(value),
                                "error_mev": float(value - test.targets[index]),
                            }
                        )
            except (ValueError, RuntimeError) as exc:
                skipped.append(
                    {
                        "group": group_name,
                        "horizon_seconds": float(horizon),
                        "reason": str(exc),
                    }
                )

    output_dir = (args.output_dir or _default_output_path(args.run_paths[0])).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "run_audit.csv", audits)
    _write_csv(output_dir / "metrics.csv", metrics)
    _write_csv(output_dir / "predictions.csv", prediction_rows)
    _write_report(
        output_dir / "report.md",
        target_id=args.target,
        lags=args.lags,
        seasonal_period=args.seasonal_period,
        audits=audits,
        groups=groups,
        missing_features=missing_features,
        split_description=split_description,
        metrics=metrics,
        skipped=skipped,
    )
    summary = {
        "target_id": args.target,
        "horizons_seconds": args.horizons,
        "lags_seconds": args.lags,
        "seasonal_period_seconds": args.seasonal_period,
        "beam_gate": {
            "fct_threshold": args.fct_threshold,
            "ict_threshold": args.ict_threshold,
        },
        "split": split_description,
        "feature_groups": groups,
        "missing_features": missing_features,
        "run_audits": audits,
        "example_counts": example_counts,
        "metrics": metrics,
        "skipped": skipped,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(finite_json(summary), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not metrics:
        print(f"No model results were produced; inspect {output_dir / 'report.md'}")
        return 2
    print(
        f"Validated {len(runs)} run(s), {len(metrics)} model results; "
        f"report: {output_dir / 'report.md'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
