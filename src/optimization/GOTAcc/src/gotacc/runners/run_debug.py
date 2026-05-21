# -*- coding: utf-8 -*-
"""
VSCode-friendly debug runner for IRFEL.

推荐用途
--------
1. 在 VSCode 里直接点运行
2. 不走 CLI
3. 方便临时改：
   - optimizer
   - runtime
   - dump config
   - 保存 config 到 JSON 文件

使用说明
--------
最常见用法：
    直接运行本文件

也可以在 main() 里改这些开关：
    DUMP_CONFIG_TO_STDOUT = True
    SAVE_CONFIG_JSON = True
    CONFIG_JSON_PATH = "save/debug_irfel_config.json"

"""

from __future__ import annotations

import json
from pathlib import Path

from gotacc.runners.optimize import run_task

def dump_config_to_stdout(cfg) -> None:
    """
    把当前 TaskConfig 以 JSON 格式打印到终端。
    """
    print("=" * 80)
    print("DUMP CONFIG (JSON)")
    print("=" * 80)
    print(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False))
    print("=" * 80)


def save_config_json(cfg, path: str | Path) -> None:
    """
    把当前 TaskConfig 保存成 JSON 文件。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"[debug_run_irfel] Config JSON saved to: {path}")


def print_run_summary(artifacts) -> None:
    """
    对运行结果做一个简单总结。
    """
    cfg = artifacts.task_config
    optimizer = artifacts.optimizer

    print("\n" + "=" * 80)
    print("RUN SUMMARY")
    print("=" * 80)
    print(f"Task name   : {cfg.meta.name}")
    print(f"Machine     : {cfg.meta.machine}")
    print(f"Backend     : {cfg.backend.type}")
    print(f"Optimizer   : {cfg.optimizer.name}")
    print(f"Bounds mode : {cfg.backend.bounds_mode}")
    print(f"Dim         : {artifacts.bounds.shape[0]}")
    print("Bounds:")
    print(artifacts.bounds)

    # 尝试打印优化器里一些常见字段
    for attr_name in ["best_x", "best_y", "best_value", "best_values"]:
        if hasattr(optimizer, attr_name):
            try:
                value = getattr(optimizer, attr_name)
                print(f"{attr_name:11s}: {value}")
            except Exception:
                pass

    print("=" * 80)


def main() -> None:
    # ==================================================================
    # 1) 读取配置
    # ==================================================================
    # a. from py
    # without validate
    # from gotacc.configs.py_cfg.para_irfel import task_config
    # cfg = task_config()
    
    # with validate
    from gotacc.configs.loader import load_task_config
    from pathlib import Path
    # cfg_path = Path(__file__).resolve().parent.parent / "configs" / "py_cfg" / "para_irfel.py"
    # cfg = load_task_config(cfg_path)
    cfg = load_task_config("gotacc.configs.py_cfg.para_irfel")

    # b from YAML 
    # from gotacc.configs.loader import load_task_config
    # cfg = load_task_config("configs/irfel_bo.yaml")

    # ==================================================================
    # 2) 调试期常用运行开关
    # ==================================================================
    cfg.runtime.verbose = True
    cfg.runtime.save_history = True
    cfg.runtime.history_path = None

    # 调试时通常建议先关自动写回 best，避免误操作机器
    cfg.runtime.set_best = False

    # 调试时也常建议先不画图，减少干扰
    cfg.runtime.plot_convergence = True
    cfg.runtime.plot_path = None

    # 出错时恢复初始值
    cfg.runtime.restore_initial_on_error = True
    cfg.runtime.restore_initial_on_keyboard_interrupt = True

    # ==================================================================
    # 3) 是否 dump config
    # ==================================================================
    DUMP_CONFIG_TO_STDOUT = True
    SAVE_CONFIG_JSON = True
    CONFIG_JSON_PATH = "save/debug_irfel_config.json"

    if DUMP_CONFIG_TO_STDOUT:
        dump_config_to_stdout(cfg)

    if SAVE_CONFIG_JSON:
        save_config_json(cfg, CONFIG_JSON_PATH)

    # ==================================================================
    # 4) 这里可以临时切算法做实验
    #
    #    注意：
    #    - 单目标算法：combine_mode 应保持 "weighted_sum"
    #    - 多目标算法：combine_mode 应改成 "vector"
    #
    #    如果你已经在 para_irfel.py 里做了 ALGO_MODE 开关，
    #    通常不建议再在这里改；
    #    但调试时临时试一把是可以的。
    # ==================================================================

    # ---- 示例 A：临时切到 TuRBO（单目标） ----
    # cfg.optimizer.name = "turbo"
    # cfg.optimizer.kwargs = {
    #     "n_init": 5,
    #     "n_iter": 15,
    #     "random_state": 120,
    #     "verbose": True,
    # }
    # cfg.backend.kwargs["combine_mode"] = "weighted_sum"

    # ---- 示例 B：临时切到 MOBO（多目标） ----
    # cfg.optimizer.name = "mobo"
    # cfg.optimizer.kwargs = {
    #     "n_init": 8,
    #     "n_iter": 20,
    #     "random_state": 120,
    #     "verbose": True,
    # }
    # cfg.backend.kwargs["combine_mode"] = "vector"
    # cfg.runtime.set_best = False

    # ---- 示例 C：临时切到 NSGA2（多目标） ----
    # cfg.optimizer.name = "nsga2"
    # cfg.optimizer.kwargs = {
    #     "pop_size": 40,
    #     "n_gen": 20,
    #     "random_state": 120,
    #     "verbose": True,
    # }
    # cfg.backend.kwargs["combine_mode"] = "vector"
    # cfg.runtime.set_best = False

    # ==================================================================
    # 5) 真正运行
    # ==================================================================
    # artifacts = run_task(cfg)

    # ==================================================================
    # 6) 结果总结
    # ==================================================================
    # print_run_summary(artifacts)


if __name__ == "__main__":
    main()