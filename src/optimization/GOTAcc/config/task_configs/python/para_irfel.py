# -*- coding: utf-8 -*-
"""
IRFEL online optimization config
Single-file switch template for:
    - BO
    - TuRBO
    - MOBO
    - NSGA2

使用方式
--------
只改下面这一行：

    ALGO_MODE = "bo"

可选值：
    "bo"
    "turbo"
    "mobo"
    "nsga2"

说明
----
1. 这个文件只保留 task_config() / TASK_CONFIG 新接口
2. 由 loader.py 直接读取
3. 多目标算法（MOBO / NSGA2）时，会自动切到 combine_mode="vector"
4. 多目标算法默认 set_best=False，避免自动写回一个人为定义的“best”
"""

from __future__ import annotations

from gotacc.configs.schema import (
    BackendConfig,
    MetaConfig,
    OptimizerConfig,
    RuntimeConfig,
    TaskConfig,
)

# =============================================================================
# 一键切换算法：改这里
# =============================================================================
ALGO_MODE = "bo"   # 可选: "bo", "turbo", "mobo", "nsga2"


# =============================================================================
# 基本信息
# =============================================================================
TASK_NAME_PREFIX = "irfel"
MACHINE_NAME = "IRFEL"


# =============================================================================
# 目标配置
# =============================================================================
def obj_para():
    """
    当前目标保持与你现有 IRFEL 配置一致：
        1) FEL:Energy
        2) IRFEL:BD:CT:CH4:WAV:ASUM

    注意
    ----
    你当前习惯是用负权重表示“越大越好/越小越好”方向，并在单目标时做 weighted_sum。

    对于多目标（vector）模式：
        backend 会返回 results * obj_weights

    因此你要确认自己的多目标优化器约定：
    - 若优化器默认“最小化”每个目标，而你又想“最大化信号”，
      那么负权重是有意义的；
    - 若你的多目标实现本身是“最大化”，那就可能需要把权重改成正的。

    """
    obj_pvnames = [
        "FEL:Energy",
        "IRFEL:BD:CT:CH4:WAV:ASUM",
    ]

    obj_weights = [-1.0, -1.0]

    obj_samples = 3
    obj_math = ["mean", "mean"]
    set_interval = 1
    sample_interval = 1

    return obj_pvnames, obj_weights, obj_samples, obj_math, set_interval, sample_interval


# =============================================================================
# knob 配置
# =============================================================================
def knob_para():
    """
    当前保持与现有 IRFEL 配置一致：
    """
    cor_x_all = [
        "IRFEL:PS:HC01:current:ao",
        "IRFEL:PS:HC02:current:ao",
        "IRFEL:PS:HC03:current:ao",
        "IRFEL:PS:HC04:current:ao",
        "IRFEL:PS:HC05:current:ao",
        "IRFEL:PS:HC06:current:ao",
        "IRFEL:PS:HC07:current:ao",
        "IRFEL:PS:HIC01:current:ao",
        "IRFEL:PS:HIC02:current:ao",
        "IRFEL:PS:MS:HC:current:ao",
    ]

    cor_y_all = [
        "IRFEL:PS:VC01:current:ao",
        "IRFEL:PS:VC02:current:ao",
        "IRFEL:PS:VC03:current:ao",
        "IRFEL:PS:VC04:current:ao",
        "IRFEL:PS:VC05:current:ao",
        "IRFEL:PS:VC06:current:ao",
        "IRFEL:PS:VC07:current:ao",
        "IRFEL:PS:VIC01:current:ao",
        "IRFEL:PS:VIC02:current:ao",
        "IRFEL:PS:MS:VC:current:ao",
    ]

    quad_all = [
        "IRFEL:PS:QM01:current:ao",
        "IRFEL:PS:QM02:current:ao",
        "IRFEL:PS:QM03:current:ao",
        "IRFEL:PS:QM04:current:ao",
        "IRFEL:PS:QM05:current:ao",
        "IRFEL:PS:QM06:current:ao",
        "IRFEL:PS:QM07:current:ao",
        "IRFEL:PS:QM08:current:ao",
        "IRFEL:PS:QM09:current:ao",
        "IRFEL:PS:QM10:current:ao",
        "IRFEL:PS:QM11:current:ao",
        "IRFEL:PS:QM12:current:ao",
        "IRFEL:PS:QM13:current:ao",
        "IRFEL:PS:QM14:current:ao",
        "IRFEL:PS:QM15:current:ao",
        "IRFEL:PS:QM16:current:ao",
        "IRFEL:PS:QM17:current:ao",
        "IRFEL:PS:QM18:current:ao",
        "IRFEL:PS:QM19:current:ao",
    ]

    cavity_all = [
        "IRFEL:IN-MW:SHB:SET_PHASE",
        "IRFEL:IN-MW:KLY1:SET_PHASE",
        "IRFEL:IN-MW:KLY2:SET_PHASE",
    ]

    sol_all = [
        "IRFEL:PS:MS01:current:ao",
        "IRFEL:PS:LS01:current:ao",
        "IRFEL:PS:SS01:current:ao",
        "IRFEL:PS:SS02:current:ao",
    ]

    bend_all = [
        "IRFEL:PS:BM04:current:ao",
        "IRFEL:PS:RBM:current:ao",
    ]

    # ------------------------------------------------------------
    # 当前实际启用的 knobs
    # ------------------------------------------------------------
    indices = [0, 1]
    cor_x_pvlist = [cor_x_all[i] for i in indices]
    cor_y_pvlist = [cor_y_all[i] for i in indices]
    cor_x_bounds = [[-8, 8]] * len(indices)
    cor_y_bounds = [[-8, 8]] * len(indices)

    indices = []
    quad_pvlist = [quad_all[i] for i in indices]
    quad_bounds = [[-8, 8]] * len(indices)

    indices = []
    cav_pvlist = [cavity_all[i] for i in indices]
    cav_bounds = [[-5, 5], [-20, 20]] if len(indices) != 0 else []

    indices = []
    sol_pvlist = [sol_all[i] for i in indices]
    sol_bounds = [[-5, 5]] * len(indices)

    indices = []
    bend_pvlist = [bend_all[i] for i in indices]
    bend_bounds = [[-5, 5]] * len(indices)

    knob_pvlist = (
        cor_x_pvlist
        + cor_y_pvlist
        + quad_pvlist
        + cav_pvlist
        + sol_pvlist
        + bend_pvlist
    )

    knob_bounds = (
        cor_x_bounds
        + cor_y_bounds
        + quad_bounds
        + cav_bounds
        + sol_bounds
        + bend_bounds
    )

    return knob_pvlist, knob_bounds


# =============================================================================
# 算法切换逻辑
# =============================================================================
def _build_algo_section(algo_mode: str):
    """
    根据 ALGO_MODE 返回：
        1) task_name_suffix
        2) description
        3) backend_kwargs_patch
        4) optimizer_config
        5) runtime_config

    这样整个 task_config() 只需要把公共部分 + 算法特定部分拼起来。
    """
    algo = algo_mode.lower().strip()

    # -------------------------------------------------------------------------
    # BO：单目标，weighted_sum
    # -------------------------------------------------------------------------
    if algo == "bo":
        return (
            "bo_weighted_fel",
            "IRFEL online BO with weighted-sum scalar objective.",
            {
                "combine_mode": "weighted_sum",
                "best_selector_mode": "max",
                "objective_policy": "fel_energy_guard",
                "objective_policy_kwargs": {
                    "target_col": 0,
                    "large_threshold": 1e6,
                    "change_threshold": 1e-6,
                },
            },
            OptimizerConfig(
                name="bo",
                kwargs={
                    "kernel_type": "matern",
                    "gp_restarts": 5,
                    "acq": "ucb",
                    "acq_para": 2.0,
                    "acq_para_kwargs": {"beta_strategy": "inv_decay", "beta_lam": 0.01},
                    "acq_optimizer": "optimize_acqf",
                    "acq_opt_kwargs": {
                        "num_restarts": 8,
                        "raw_samples": 256,
                        "n_candidates": 8192,
                    },
                    "n_init": 5,
                    "n_iter": 15,
                    "random_state": 120,
                    "verbose": True,
                },
            ),
            RuntimeConfig(
                save_history=True,
                history_path=None,
                plot_convergence=True,
                plot_path=None,
                set_best=True,
                restore_initial_on_error=True,
                verbose=True,
            ),
        )

    # -------------------------------------------------------------------------
    # TuRBO：单目标，weighted_sum
    # -------------------------------------------------------------------------
    if algo == "turbo":
        return (
            "turbo_weighted_fel",
            "IRFEL online TuRBO with weighted-sum scalar objective.",
            {
                "combine_mode": "weighted_sum",
                "best_selector_mode": "max",
                "objective_policy": "fel_energy_guard",
                "objective_policy_kwargs": {
                    "target_col": 0,
                    "large_threshold": 1e6,
                    "change_threshold": 1e-6,
                },
            },
            OptimizerConfig(
                name="turbo",
                kwargs={
                    "n_init": 5,
                    "n_iter": 15,
                    "random_state": 120,
                    "verbose": True,

                    # 下面这些参数按你的 TuRBOOptimizer 实现取舍
                    # "batch_size": 1,
                    # "success_tolerance": 3,
                    # "failure_tolerance": 5,
                    # "length_init": 0.8,
                    # "length_min": 0.5**7,
                    # "length_max": 1.6,
                },
            ),
            RuntimeConfig(
                save_history=True,
                history_path=None,
                plot_convergence=True,
                plot_path=None,
                set_best=True,
                restore_initial_on_error=True,
                verbose=True,
            ),
        )

    # -------------------------------------------------------------------------
    # MOBO：多目标，vector
    # -------------------------------------------------------------------------
    if algo == "mobo":
        return (
            "mobo_vector_fel",
            "IRFEL online MOBO with vector objective.",
            {
                "combine_mode": "vector",
                "best_selector_mode": "sum_min",
                # "write_policy": "equal",
                # "write_policy_kwargs": {    
                #     "pvlinks": [
                #         (11, "IRFEL:PS:QM15:current:ao"),
                #         (10, "IRFEL:PS:QM16:current:ao"),
                #     ]},
                # "objective_policy": "zero_guard",
                # "objective_policy_kwargs": {
                #     "target_col": 1,
                #     "zero_atol": 1e-12,
                #     "offset": 100.0,
                # }
                "objective_policy": "fel_energy_guard",
                "objective_policy_kwargs": {
                    "target_col": 0,
                    "large_threshold": 1e6,
                    "change_threshold": 1e-6,
                },
            },
            OptimizerConfig(
                name="mobo",
                kwargs={
                    "n_init": 8,
                    "n_iter": 20,
                    "random_state": 120,
                    "verbose": True,

                    # 下面按你的 MOBOOptimizer 实现取舍
                    # "ref_point": [-1.0, -1.0],
                    # "acq": "ehvi",
                    # "batch_size": 1,
                },
            ),
            RuntimeConfig(
                save_history=True,
                history_path=None,
                plot_convergence=False,
                plot_path=None,

                # 多目标不建议自动写回单个“best”
                set_best=False,

                restore_initial_on_error=True,
                verbose=True,
            ),
        )

    # -------------------------------------------------------------------------
    # NSGA2：多目标，vector
    # -------------------------------------------------------------------------
    if algo == "nsga2":
        return (
            "nsga2_vector_fel",
            "IRFEL online NSGA2 with vector objective.",
            {
                "combine_mode": "vector",
                "best_selector_mode": "sum_min",
                "objective_policy": "fel_energy_guard",
                "objective_policy_kwargs": {
                    "target_col": 0,
                    "large_threshold": 1e6,
                    "change_threshold": 1e-6,
                },
            },
            OptimizerConfig(
                name="nsga2",
                kwargs={
                    "pop_size": 40,
                    "n_gen": 20,
                    "random_state": 120,
                    "verbose": True,

                    # 下面按你的 NSGA2Optimizer 实现取舍
                    "crossover_eta": 20,
                    "mutation_eta": 20,
                    "mutation_prob": None,
                },
            ),
            RuntimeConfig(
                save_history=True,
                history_path=None,
                plot_convergence=False,
                plot_path=None,
                set_best=False,
                restore_initial_on_error=True,
                verbose=True,
            ),
        )

    raise ValueError(
        f"Unsupported ALGO_MODE={algo_mode!r}. "
        "Expected one of: 'bo', 'turbo', 'mobo', 'nsga2'."
    )


# =============================================================================
# 主配置出口
# =============================================================================
def task_config() -> TaskConfig:
    knobs_pvnames, knobs_bounds = knob_para()
    obj_pvnames, obj_weights, obj_samples, obj_math, set_interval, sample_interval = obj_para()

    task_suffix, description, backend_patch, optimizer_cfg, runtime_cfg = _build_algo_section(ALGO_MODE)

    backend_kwargs = {
        "knobs_pvnames": knobs_pvnames,
        "obj_pvnames": obj_pvnames,
        "obj_weights": obj_weights,
        "obj_samples": obj_samples,
        "obj_math": obj_math,
        "set_interval": set_interval,
        "sample_interval": sample_interval,

        # 通用 EPICS backend 行为
        "log_path": "template.opt",
        "readback_check": False,
        "readback_tol": 1e-6,
    }

    backend_kwargs.update(backend_patch)

    return TaskConfig(
        meta=MetaConfig(
            name=f"{TASK_NAME_PREFIX}_{task_suffix}",
            machine=MACHINE_NAME,
            description=description,
        ),
        backend=BackendConfig(
            type="epics",
            bounds=knobs_bounds,
            bounds_mode="relative",
            kwargs=backend_kwargs,
        ),
        optimizer=optimizer_cfg,
        runtime=runtime_cfg,
    )


# 可选：让 loader 也能直接读取模块级常量
TASK_CONFIG = task_config()


if __name__ == "__main__":
    print(TASK_CONFIG)
# 如何写policy
# "objective_policy": "zero_guard",
# "objective_policy_kwargs": {
#     "target_col": 1,
#     "zero_atol": 1e-12,
#     "offset": 100.0,
# }

# "objective_policy": "fel_energy_guard",
# "objective_policy_kwargs": {
#     "target_col": 0,
#     "large_threshold": 1e6,
#     "change_threshold": 1e-6,
# }

# "objective_policy": "zero_guard",
# "objective_policy_kwargs": {
#     "target_col": 1,
#     "zero_atol": 1e-12,
#     "offset": 100.0,
# }

# "objective_policies": [
#     {
#         "name": "fel_energy_guard",
#         "kwargs": {
#             "target_col": 0,
#             "large_threshold": 1e6,
#             "change_threshold": 1e-6,
#         },
#     },
#     {
#         "name": "zero_guard",
#         "kwargs": {
#             "target_col": 1,
#             "zero_atol": 1e-12,
#             "offset": 100.0,
#         },
#     },
#     {
#         "name": "zero_guard",
#         "kwargs": {
#             "target_col": 0,
#             "zero_atol": 1e-10,
#             "offset": 50.0,
#         },
#     },
# ]
