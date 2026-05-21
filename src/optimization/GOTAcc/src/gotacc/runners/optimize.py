from __future__ import annotations

"""
Unified optimization runner for GOTAcc.

当前只服务统一主链：
    TaskConfig
        -> build_backend()
        -> resolve_bounds()
        -> build_optimizer()
        -> optimizer.optimize()

设计原则
--------
1. runner 只负责“执行任务”，不再兼容旧接口
2. backend 必须实现统一方法：
       - init_knob_value()
       - evaluate()
       - restore_initial()      (可选，但若 runtime 要恢复则建议实现)
       - set_best(optimizer=None) (可选)
3. optimizer 由 config 显式指定，不做隐式猜测
4. 对配置/接口不匹配尽早报错，而不是静默兜底
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from gotacc.configs.schema import TaskConfig
from gotacc.interfaces.factory import build_backend


# =============================================================================
# run result bundle
# =============================================================================
@dataclass
class RunArtifacts:
    """
    一次优化任务运行完成后返回的对象。

    字段说明
    --------
    task_config:
        本次运行使用的统一配置

    backend:
        实际构造出来的 backend 对象

    optimizer:
        实际构造出来的 optimizer 对象

    x0:
        backend 返回的初始点（绝对坐标）
        对 offline/online 都统一保留，便于调试

    bounds:
        最终传给 optimizer 的绝对边界，shape = (dim, 2)

    optimize_result:
        optimizer.optimize() 的原始返回值
    """
    task_config: TaskConfig
    backend: Any
    optimizer: Any
    x0: np.ndarray
    bounds: np.ndarray
    optimize_result: Any


# =============================================================================
# optimizer builder
# =============================================================================
def build_optimizer(task_cfg: TaskConfig, objective_callable, bounds: np.ndarray):
    """
    根据 task_cfg.optimizer 构造优化器。

    这里刻意只做一层薄分发：
    - optimizer 的选择由 config 决定
    - runner 不去猜测算法意图
    """
    ocfg = task_cfg.optimizer
    name = str(ocfg.name).lower()
    kwargs = dict(ocfg.kwargs)

    if name in {"bo", "bayesopt", "bayesian_optimization"}:
        from gotacc.algorithms.single_objective.bo import BOOptimizer
        return BOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"consbo", "constrained_bo", "constrained_bayesian_optimization"}:
        from gotacc.algorithms.single_objective.consbo import ConsBOOptimizer
        return ConsBOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"turbo", "trust_region_bo"}:
        from gotacc.algorithms.single_objective.turbo import TuRBOOptimizer
        return TuRBOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"rcds"}:
        from gotacc.algorithms.single_objective.rcds import RCDSOptimizer
        return RCDSOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"smggpo", "mggpo_so", "single_objective_mggpo", "single_objective_mg-gpo"}:
        from gotacc.algorithms.single_objective.mggpo_so import MGGPOSOOptimizer
        return MGGPOSOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {
        "consmggpo_so",
        "constrained_mggpo_so",
        "single_objective_consmggpo",
        "single_objective_constrained_mggpo",
        "single_objective_constrained_mg-gpo",
    }:
        from gotacc.algorithms.single_objective.consmggpo_so import ConsMGGPOSOOptimizer
        return ConsMGGPOSOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"mobo"}:
        from gotacc.algorithms.multi_objective.mobo import MOBOOptimizer
        return MOBOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"consmobo", "constrained_mobo", "constrained_multi_objective_bo"}:
        from gotacc.algorithms.multi_objective.consmobo import ConsMOBOOptimizer
        return ConsMOBOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"consmggpo", "constrained_mggpo", "constrained_mg-gpo"}:
        from gotacc.algorithms.multi_objective.consmggpo import ConsMGGPOOptimizer
        return ConsMGGPOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"mggpo"}:
        from gotacc.algorithms.multi_objective.mggpo import MGGPOOptimizer
        return MGGPOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"mopso"}:
        from gotacc.algorithms.multi_objective.mopso import MOPSOOptimizer
        return MOPSOOptimizer(func=objective_callable, bounds=bounds, **kwargs)

    if name in {"nsga2", "nsga-ii"}:
        from gotacc.algorithms.multi_objective.nsga2 import NSGA2Optimizer
        return NSGA2Optimizer(func=objective_callable, bounds=bounds, **kwargs)

    raise ValueError(f"Unknown optimizer name: {task_cfg.optimizer.name!r}")


# =============================================================================
# bounds resolver
# =============================================================================
def resolve_bounds(task_cfg: TaskConfig, x0: np.ndarray) -> np.ndarray:
    """
    将 TaskConfig.backend.bounds 解析成最终传给 optimizer 的绝对边界。

    规则
    ----
    bounds_mode == "absolute":
        backend.bounds 直接作为最终边界

    bounds_mode == "relative":
        最终边界 = x0 + relative_bounds

    注意
    ----
    schema.py 已经保证 bounds 一定存在，因此这里不再做 None 兼容。
    """
    bounds = np.asarray(task_cfg.backend.bounds, dtype=float)

    if bounds.ndim != 2 or bounds.shape[1] != 2:
        raise ValueError(
            f"backend.bounds must have shape (dim, 2), got {bounds.shape}"
        )

    mode = task_cfg.backend.bounds_mode

    if mode == "absolute":
        return bounds

    if mode == "relative":
        x0 = np.asarray(x0, dtype=float).reshape(-1)

        if len(x0) != bounds.shape[0]:
            raise ValueError(
                f"Relative bounds dimension mismatch: len(x0)={len(x0)}, "
                f"bounds dim={bounds.shape[0]}"
            )

        return np.column_stack([x0 + bounds[:, 0], x0 + bounds[:, 1]])

    raise ValueError(f"Unsupported bounds_mode: {mode!r}")


# =============================================================================
# optimizer/backend consistency checks
# =============================================================================
def validate_optimizer_backend_match(task_cfg: TaskConfig) -> None:
    """
    对 optimizer 与 backend 关键配置做一些“早失败”检查。

    当前主要检查：
    - 单目标优化器应搭配 weighted_sum
    - 多目标优化器应搭配 vector

    注意
    ----
    这里依赖 backend.kwargs 中显式提供 combine_mode。
    这是当前 config 约定的一部分。
    """
    optimizer_name = str(task_cfg.optimizer.name).lower()
    combine_mode = str(task_cfg.backend.kwargs.get("combine_mode", "weighted_sum")).lower()

    single_objective_optimizers = {
        "bo", "bayesopt", "bayesian_optimization",
        "consbo", "constrained_bo", "constrained_bayesian_optimization",
        "turbo", "trust_region_bo",
        "rcds",
        "smggpo", "mggpo_so", "single_objective_mggpo", "single_objective_mg-gpo",
        "consmggpo_so", "constrained_mggpo_so",
        "single_objective_consmggpo",
        "single_objective_constrained_mggpo",
        "single_objective_constrained_mg-gpo",
    }

    multi_objective_optimizers = {
        "mobo",
        "consmobo", "constrained_mobo", "constrained_multi_objective_bo",
        "consmggpo", "constrained_mggpo", "constrained_mg-gpo",
        "mggpo",
        "mopso",
        "nsga2", "nsga-ii",
    }

    if optimizer_name in single_objective_optimizers and combine_mode != "weighted_sum":
        raise ValueError(
            f"Single-objective optimizer {task_cfg.optimizer.name!r} requires "
            f"backend combine_mode='weighted_sum', got {combine_mode!r}"
        )

    if optimizer_name in multi_objective_optimizers and combine_mode != "vector":
        raise ValueError(
            f"Multi-objective optimizer {task_cfg.optimizer.name!r} requires "
            f"backend combine_mode='vector', got {combine_mode!r}"
        )


# =============================================================================
# helper functions
# =============================================================================
def resolve_history_path(task_cfg: TaskConfig) -> str:
    """
    若 runtime.history_path 未显式指定，则生成一个默认路径。
    """
    if task_cfg.runtime.history_path:
        return task_cfg.runtime.history_path

    task_name = task_cfg.meta.name or "task"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"save/{task_name}_{ts}.dat"


def save_history_if_possible(optimizer: Any, history_path: str, verbose: bool = True) -> None:
    """
    若 optimizer 提供 save_history()，则保存历史记录。
    """
    if not hasattr(optimizer, "save_history"):
        if verbose:
            print("[GOTAcc] Optimizer has no save_history(); skip history saving.")
        return

    Path(history_path).parent.mkdir(parents=True, exist_ok=True)
    optimizer.save_history(history_path)

    if verbose:
        print(f"[GOTAcc] History saved to: {history_path}")


def plot_convergence_if_possible(optimizer: Any, plot_path: str | None, verbose: bool = True) -> None:
    """
    若 optimizer 提供 plot_convergence()，则调用之。

    当前不强制所有 optimizer 必须支持 plot_convergence()。
    """
    if not hasattr(optimizer, "plot_convergence"):
        if verbose:
            print("[GOTAcc] Optimizer has no plot_convergence(); skip plotting.")
        return

    # 兼容常见两类实现：
    #   1. plot_convergence()
    #   2. plot_convergence(path=...)
    if plot_path is None:
        optimizer.plot_convergence()
    else:
        try:
            optimizer.plot_convergence(path=plot_path)
        except TypeError:
            optimizer.plot_convergence()

    if verbose:
        if plot_path is None:
            print("[GOTAcc] Convergence plot generated.")
        else:
            print(f"[GOTAcc] Convergence plot generated (requested path: {plot_path}).")


def restore_initial_if_possible(backend: Any, verbose: bool = True) -> None:
    """
    若 backend 提供 restore_initial()，则调用之。
    否则直接跳过。
    """
    if not hasattr(backend, "restore_initial"):
        if verbose:
            print("[GOTAcc] Backend has no restore_initial(); skip restore.")
        return

    backend.restore_initial()

    if verbose:
        print("[GOTAcc] Backend restored to initial state.")


def set_best_if_possible(backend: Any, optimizer: Any, verbose: bool = True) -> None:
    """
    若 backend 提供 set_best()，则调用之。

    现在约定统一接口为：
        backend.set_best(optimizer=None)

    不再做旧接口签名猜测。
    """
    if not hasattr(backend, "set_best"):
        if verbose:
            print("[GOTAcc] Backend has no set_best(); skip applying best point.")
        return

    backend.set_best(optimizer=optimizer)

    if verbose:
        print("[GOTAcc] Best point has been applied by backend.set_best().")


def close_backend_if_possible(backend: Any) -> None:
    """
    若 backend 提供 close()，则调用之。
    """
    if hasattr(backend, "close"):
        backend.close()


# =============================================================================
# main runner
# =============================================================================
def run_task(task_cfg: TaskConfig) -> RunArtifacts:
    """
    执行一个完整优化任务。

    流程
    ----
    1. 检查 optimizer / backend 关键配置是否匹配
    2. build backend
    3. 读取初始点 x0
    4. 解析最终绝对边界
    5. 构造 optimizer
    6. optimize()
    7. 可选保存 history / 画图 / set_best
    8. 返回 RunArtifacts
    """
    validate_optimizer_backend_match(task_cfg)

    backend = build_backend(task_cfg)

    if not hasattr(backend, "init_knob_value"):
        raise TypeError("Backend must implement init_knob_value().")

    if not hasattr(backend, "evaluate"):
        raise TypeError("Backend must implement evaluate().")

    x0 = np.asarray(backend.init_knob_value(), dtype=float).reshape(-1)
    bounds = resolve_bounds(task_cfg, x0)

    optimizer_name = str(task_cfg.optimizer.name).lower()
    objective_callable = backend.evaluate
    if optimizer_name in {
        "consbo",
        "constrained_bo",
        "constrained_bayesian_optimization",
        "consmggpo_so",
        "constrained_mggpo_so",
        "single_objective_consmggpo",
        "single_objective_constrained_mggpo",
        "single_objective_constrained_mg-gpo",
        "consmobo",
        "constrained_mobo",
        "constrained_multi_objective_bo",
        "consmggpo",
        "constrained_mggpo",
        "constrained_mg-gpo",
    }:
        if not hasattr(backend, "evaluate_with_constraints"):
            raise TypeError("Constrained optimizer requires backend.evaluate_with_constraints().")
        objective_callable = backend.evaluate_with_constraints

    optimizer = build_optimizer(
        task_cfg=task_cfg,
        objective_callable=objective_callable,
        bounds=bounds,
    )

    if task_cfg.runtime.verbose:
        print("=" * 72)
        print(f"Task      : {task_cfg.meta.name}")
        print(f"Machine   : {task_cfg.meta.machine}")
        print(f"Backend   : {task_cfg.backend.type}")
        print(f"Optimizer : {task_cfg.optimizer.name}")
        print(f"Bounds    :\n{bounds}")
        print("=" * 72)

    try:
        optimize_result = optimizer.optimize()

        if task_cfg.runtime.save_history:
            history_path = resolve_history_path(task_cfg)
            save_history_if_possible(
                optimizer,
                history_path=history_path,
                verbose=task_cfg.runtime.verbose,
            )

        if task_cfg.runtime.plot_convergence:
            plot_convergence_if_possible(
                optimizer,
                plot_path=task_cfg.runtime.plot_path,
                verbose=task_cfg.runtime.verbose,
            )

        if task_cfg.runtime.set_best:
            set_best_if_possible(
                backend,
                optimizer=optimizer,
                verbose=task_cfg.runtime.verbose,
            )

        return RunArtifacts(
            task_config=task_cfg,
            backend=backend,
            optimizer=optimizer,
            x0=x0,
            bounds=bounds,
            optimize_result=optimize_result,
        )

    except KeyboardInterrupt:
        if task_cfg.runtime.restore_initial_on_keyboard_interrupt:
            restore_initial_if_possible(
                backend,
                verbose=task_cfg.runtime.verbose,
            )
        raise

    except Exception:
        if task_cfg.runtime.restore_initial_on_error:
            restore_initial_if_possible(
                backend,
                verbose=task_cfg.runtime.verbose,
            )
        raise

    finally:
        close_backend_if_possible(backend)
