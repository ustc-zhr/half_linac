from __future__ import annotations

"""
GOTAcc CLI entry: run one optimization task from a unified TaskConfig.

设计目标
--------
读取 task_config -> 可选覆盖少量 runtime 参数 -> 调用统一 runner

you can run it by CLI (example):
    python -m gotacc.runners.run_cli --config path/to/irfel_bo.py
    python -m gotacc.runners.run_cli --config path/to/irfel_bo.yaml

"""

import argparse
import json
from pathlib import Path
from typing import Any

from gotacc.configs.loader import load_task_config
from gotacc.runners.optimize import run_task


# =============================================================================
# CLI 参数解析
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    """
    构造命令行参数解析器。

    目前只保留与“运行任务”直接相关的少量参数：
    - --config        配置来源（必须）
    - --set-best      是否在结束后把 best 写回机器
    - --plot          是否画收敛图
    - --history-path  覆盖 history 保存路径
    - --quiet         关闭大部分输出
    - --dump-config   先打印规范化后的 TaskConfig
    """
    parser = argparse.ArgumentParser(
        description="Run a GOTAcc optimization task from TaskConfig."
    )

    parser.add_argument(
        "--config",
        required=True,
        help=(
            "Task config source. Supported forms:\n"
            "  1) path/to/config.yaml\n"
            "  2) path/to/config.py\n"
            "  3) python.module.path\n\n"
            "The config module/file must provide either:\n"
            "  - TASK_CONFIG = TaskConfig(...)\n"
            "  - def task_config() -> TaskConfig"
        ),
    )

    # -------------------------------------------------------------------------
    # runtime 覆盖项：set_best
    # -------------------------------------------------------------------------
    parser.add_argument(
        "--set-best",
        dest="set_best",
        action="store_true",
        default=None,
        help="Override runtime.set_best=True",
    )
    parser.add_argument(
        "--no-set-best",
        dest="set_best",
        action="store_false",
        help="Override runtime.set_best=False",
    )

    # -------------------------------------------------------------------------
    # runtime 覆盖项：plot_convergence
    # -------------------------------------------------------------------------
    parser.add_argument(
        "--plot",
        dest="plot_convergence",
        action="store_true",
        default=None,
        help="Override runtime.plot_convergence=True",
    )
    parser.add_argument(
        "--no-plot",
        dest="plot_convergence",
        action="store_false",
        help="Override runtime.plot_convergence=False",
    )

    # -------------------------------------------------------------------------
    # runtime 覆盖项：history_path
    # -------------------------------------------------------------------------
    parser.add_argument(
        "--history-path",
        type=str,
        default=None,
        help="Override runtime.history_path",
    )

    # -------------------------------------------------------------------------
    # runtime 覆盖项：verbose
    # -------------------------------------------------------------------------
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Override runtime.verbose=False",
    )

    # -------------------------------------------------------------------------
    # 辅助调试：打印规范化配置
    # -------------------------------------------------------------------------
    parser.add_argument(
        "--dump-config",
        action="store_true",
        help="Print normalized TaskConfig as JSON before running",
    )

    return parser


# =============================================================================
# runtime 覆盖逻辑
# =============================================================================
def apply_runtime_overrides(task_cfg, args: argparse.Namespace):
    """
    将 CLI 上提供的少量 runtime 覆盖项写回 task_cfg。

    这里故意只允许覆盖 runtime 层的几个行为参数，
    不允许在 CLI 里临时改 backend / optimizer 的核心结构。
    这样可以保证配置来源单一、逻辑清晰。
    """
    if args.set_best is not None:
        task_cfg.runtime.set_best = args.set_best

    if args.plot_convergence is not None:
        task_cfg.runtime.plot_convergence = args.plot_convergence

    if args.history_path is not None:
        task_cfg.runtime.history_path = args.history_path

    if args.quiet:
        task_cfg.runtime.verbose = False

    return task_cfg


# =============================================================================
# 结果打印
# =============================================================================
def print_run_summary(artifacts: Any) -> None:
    """
    对 run_task() 返回的 RunArtifacts 做一个简洁总结输出。

    这里只打印一些通用信息，不假设所有 optimizer 都有相同的 best_x / best_y 字段。
    """
    task_cfg = artifacts.task_config
    optimizer = artifacts.optimizer
    bounds = artifacts.bounds

    print("[GOTAcc] Task finished successfully.")
    print(f"[GOTAcc] Task name   : {task_cfg.meta.name}")
    print(f"[GOTAcc] Machine     : {task_cfg.meta.machine}")
    print(f"[GOTAcc] Backend     : {task_cfg.backend.type}")
    print(f"[GOTAcc] Optimizer   : {task_cfg.optimizer.name}")
    print(f"[GOTAcc] Dim         : {bounds.shape[0]}")
    print(f"[GOTAcc] Bounds mode : {task_cfg.backend.bounds_mode}")

    # 尽量做“非侵入式”地读一些常见字段；读不到就跳过
    for attr_name in ["best_x", "best_y", "best_value", "best_values"]:
        if hasattr(optimizer, attr_name):
            try:
                value = getattr(optimizer, attr_name)
                print(f"[GOTAcc] {attr_name:11s}: {value}")
            except Exception:
                pass


# =============================================================================
# main
# =============================================================================
def main() -> None:
    """
    CLI 主入口。

    流程：
    1. 解析参数
    2. 读取 TaskConfig
    3. 应用少量 runtime 覆盖项
    4. 可选打印配置
    5. 调用 run_task()
    6. 打印总结
    """
    parser = build_parser()
    args = parser.parse_args()

    # 1) 统一加载 TaskConfig
    task_cfg = load_task_config(args.config)

    # 2) 应用少量 runtime 层覆盖
    task_cfg = apply_runtime_overrides(task_cfg, args)

    # 3) 可选打印规范化配置
    if args.dump_config:
        print(json.dumps(task_cfg.to_dict(), indent=2, ensure_ascii=False))

    # 4) 统一 runner 执行
    artifacts = run_task(task_cfg)

    # 5) 输出结果总结
    if task_cfg.runtime.verbose:
        print_run_summary(artifacts)


if __name__ == "__main__":
    main()