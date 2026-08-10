# -*- coding: utf-8 -*-
"""
HALF online optimization config (task_config only)

说明
----
1. 只保留 task_config() 作为统一出口
2. 保留 obj_para() / knob_para() 作为配置辅助函数
3. 当前任务是：
   - EPICS online
   - 单目标 BO
   - 目标为 sigx / sigy 的加权和（weighted_sum）
"""

from __future__ import annotations

from gotacc.configs.schema import (
    BackendConfig,
    MetaConfig,
    OptimizerConfig,
    RuntimeConfig,
    TaskConfig,
)


TASK_NAME = "half_bo_sigxy"
MACHINE_NAME = "HALF"
DESCRIPTION = "HALF online BO on screen sigx/sigy with weighted-sum scalar objective."


def obj_para():
    """
    目标配置

    用负权重表示“越小越好”：
        y = dot(results, weights)
    例如 sigx, sigy 都希望越小越好，则权重都设为 -1。
    """
    obj_pvnames = ["HALF:IN:FLAG:PRF07:sigx", "HALF:IN:FLAG:PRF07:sigy"]
    obj_weights = [-1.0, -1.0]
    obj_samples = 3
    obj_math = ["mean", "mean"]
    set_interval = 1
    sample_interval = 1

    return obj_pvnames, obj_weights, obj_samples, obj_math, set_interval, sample_interval


def knob_para():
    """
    决策变量配置

    当前保持与你现有 HALF 文件一致：
    - 选取 4 个水平 corrector
    - 选取 4 个垂直 corrector
    - 其余 quads / cavity / bends 暂不启用
    """
    cor_x_all = [
        "LA_PS_HIC1:current:ao", "LA_PS_HIC2:current:ao", "LA_PS_HIC3:current:ao",
        "LA_PS_HC1:current:ao", "LA_PS_HC2:current:ao", "LA_PS_HC3:current:ao", "LA_PS_HC4:current:ao",
        "LA_PS_HC5:current:ao", "LA_PS_HC6:current:ao", "LA_PS_HC7:current:ao", "LA_PS_HC8:current:ao",
        "TL_PS_HC1:current:ao", "TL_PS_HC2:current:ao", "TL_PS_HC3:current:ao", "TL_PS_HC4:current:ao",
        "TL_PS_HC5:current:ao", "TL_PS_HC6:current:ao", "TL_PS_HC7:current:ao", "TL_PS_HC8:current:ao", "TL_PS_HC9:current:ao",
    ]

    cor_y_all = [
        "LA_PS_VIC1:current:ao", "LA_PS_VIC2:current:ao", "LA_PS_VIC3:current:ao",
        "LA_PS_VC1:current:ao", "LA_PS_VC2:current:ao", "LA_PS_VC3:current:ao", "LA_PS_VC4:current:ao",
        "LA_PS_VC5:current:ao", "LA_PS_VC6:current:ao", "LA_PS_VC7:current:ao", "LA_PS_VC8:current:ao",
        "TL_PS_VC1:current:ao", "TL_PS_VC2:current:ao", "TL_PS_VC3:current:ao", "TL_PS_VC4:current:ao",
        "TL_PS_VC5:current:ao", "TL_PS_VC6:current:ao", "TL_PS_VC7:current:ao", "TL_PS_VC8:current:ao", "TL_PS_VC9:current:ao",
    ]

    quad_all = [
        "LA_PS_Q1:current:ao", "LA_PS_Q2:current:ao", "LA_PS_Q3:current:ao", "LA_PS_Q4:current:ao",
        "LA_PS_Q5:current:ao", "LA_PS_Q6:current:ao", "LA_PS_Q7:current:ao", "LA_PS_Q8:current:ao",
        "LA_PS_Q9:current:ao", "LA_PS_Q10:current:ao", "LA_PS_Q11:current:ao", "LA_PS_Q12:current:ao",
        "LA_PS_AQ2:current:ao", "LA_PS_AQ3:current:ao",
        "TL_PS_Q1:current:ao", "TL_PS_Q2:current:ao", "TL_PS_Q3:current:ao", "TL_PS_Q4:current:ao",
        "TL_PS_Q5:current:ao", "TL_PS_Q6:current:ao", "TL_PS_Q7:current:ao", "TL_PS_Q8:current:ao",
        "TL_PS_Q9:current:ao", "TL_PS_Q10:current:ao", "TL_PS_Q11:current:ao", "TL_PS_Q12:current:ao",
    ]

    cavity_all = [
        "LA-PCI-KLY1:Elecontrolphaser", "LA-PCI-KLY2:Elecontrolphaser", "LA-PCI-KLY3:Elecontrolphaser", "LA-PCI-KLY4:Elecontrolphaser",
        "LA-PCI-KLY5:Elecontrolphaser", "LA-PCI-KLY6:Elecontrolphaser", "LA-PCI-KLY7:Elecontrolphaser", "LA-PCI-KLY8:Elecontrolphaser",
        "LA-PCI-KLY9:Elecontrolphaser",
    ]

    bend_all = [
        "TL_PS_SM:current:ao", "TL_PS_AM:current:ao",
        "TL_PS_BM1256:current:ao", "TL_PS_BM1:current:ao", "TL_PS_BM2:current:ao", "TL_PS_BM5:current:ao", "TL_PS_BM6:current:ao",
        "TL_PS_BM34:current:ao",
    ]

    # 当前实际使用：4 个 X + 4 个 Y corrector
    indices = [7, 8, 9, 10]
    cor_x_pvlist = [cor_x_all[i] for i in indices]
    cor_y_pvlist = [cor_y_all[i] for i in indices]
    cor_x_bounds = [[-3, 3]] * len(indices)
    cor_y_bounds = [[-3, 3]] * len(indices)

    indices = []
    quad_pvlist = [quad_all[i] for i in indices]
    quad_bounds = [[-10, 10]] * len(indices) if len(indices) > 0 else []

    indices = []
    cavity_pvlist = [cavity_all[i] for i in indices]
    cavity_bounds = [[-10, 10]] * len(indices) if len(indices) > 0 else []

    indices = []
    bend_pvlist = [bend_all[i] for i in indices]
    bend_bounds = [[-10, 10]] * len(indices) if len(indices) > 0 else []

    knob_pvlist = cor_x_pvlist + cor_y_pvlist + quad_pvlist + cavity_pvlist + bend_pvlist
    knob_bounds = cor_x_bounds + cor_y_bounds + quad_bounds + cavity_bounds + bend_bounds

    return knob_pvlist, knob_bounds


def task_config() -> TaskConfig:
    """
    新统一出口：
    由 loader 直接调用 task_config() 获得完整 TaskConfig。
    """
    knobs_pvnames, knobs_bounds = knob_para()
    obj_pvnames, obj_weights, obj_samples, obj_math, set_interval, sample_interval = obj_para()

    return TaskConfig(
        meta=MetaConfig(
            name=TASK_NAME,
            machine=MACHINE_NAME,
            description=DESCRIPTION,
        ),
        backend=BackendConfig(
            type="epics",
            bounds=knobs_bounds,
            bounds_mode="relative",
            kwargs={
                "knobs_pvnames": knobs_pvnames,
                "obj_pvnames": obj_pvnames,
                "obj_weights": obj_weights,
                "obj_samples": obj_samples,
                "obj_math": obj_math,
                "set_interval": set_interval,
                "sample_interval": sample_interval,
                # 这几个字段由 factory.py 吸收后传给 EpicsObjective
                "log_path": "template.opt",
                "readback_check": False,
                "readback_tol": 1e-6,
                # HALF 当前是普通单目标
                "combine_mode": "weighted_sum",
                "best_selector_mode": "max",
            },
        ),
        optimizer=OptimizerConfig(
            name="bo",
            kwargs={
                "kernel_type": "matern",
                "gp_restarts": 5,
                "acq": "ucb",
                "acq_para": 2.0,
                "acq_para_kwargs": {"beta_strategy": "inv_decay", "beta_lam": 0.01},
                "acq_optimizer": "optimize_acqf",
                "acq_opt_kwargs": {"num_restarts": 8, "raw_samples": 256, "n_candidates": 8192},
                "n_init": 5,
                "n_iter": 15,
                "random_state": 120,
                "verbose": True,
            },
        ),
        runtime=RuntimeConfig(
            save_history=True,
            history_path=None,
            plot_convergence=True,
            plot_path=None,
            set_best=True,
            restore_initial_on_error=True,
            verbose=True,
        ),
    )


if __name__ == "__main__":
    print(task_config())
