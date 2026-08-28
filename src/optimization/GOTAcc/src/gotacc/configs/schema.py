from __future__ import annotations

"""
Unified task configuration schema for GOTAcc.

TaskConfig
    -> loader
    -> factory
    -> runner
    -> optimizer

设计原则
--------
1. 配置对象尽量简单、稳定
2. backend / optimizer / runtime 职责分明
3. 保留 from_dict()/to_dict()，用于 YAML / JSON / 调试输出
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


# =============================================================================
# Meta
# =============================================================================
@dataclass
class MetaConfig:
    """
    任务的描述性信息，不参与具体计算逻辑。
    """
    name: str
    machine: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)


# =============================================================================
# Backend
# =============================================================================
@dataclass
class BackendConfig:
    """
    backend 配置：描述“目标函数如何产生”。

    字段说明
    --------
    type:
        backend 类型。
        当前推荐：
            - "epics"
            - "offline"

    bounds:
        优化变量边界，shape = (dim, 2)

    bounds_mode:
        - "absolute": bounds 已经是绝对边界
        - "relative": bounds 是相对初始值的偏移量

    kwargs:
        传给 backend 构造函数的参数。
        例如 EPICS backend 常见字段：
            knobs_pvnames
            obj_pvnames
            objective_names
            obj_weights
            obj_samples
            obj_math
            set_interval
            sample_interval
            log_path
            readback_check
            readback_tol
            combine_mode
            objective_policy
            objective_policy_kwargs
            objective_policies
            constraint_policy
            constraint_policy_kwargs
            constraint_policies
            constraint_bounds
            constraint_names
            write_policy
            write_policy_kwargs
            best_selector_mode
    """
    type: str
    bounds: list[list[float]]
    bounds_mode: str = "relative"
    kwargs: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Optimizer
# =============================================================================
@dataclass
class OptimizerConfig:
    """
    optimizer 配置：描述“用什么优化器、配什么参数”。
    """
    name: str
    kwargs: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Runtime
# =============================================================================
@dataclass
class RuntimeConfig:
    """
    runtime 配置：描述“怎么运行”，而不是“优化问题本身是什么”。

    注意：
    ----
    这里不再放 EPICS backend 专属字段（例如 obj_log_path/readback_check），
    这些应属于 backend.kwargs。
    """
    save_history: bool = True
    history_path: str | None = None

    plot_convergence: bool = False
    plot_path: str | None = None

    set_best: bool = True
    restore_initial_on_error: bool = True
    restore_initial_on_keyboard_interrupt: bool = True

    verbose: bool = True


# =============================================================================
# Task
# =============================================================================
@dataclass
class TaskConfig:
    """
    一个完整优化任务的统一配置对象。
    """
    meta: MetaConfig
    backend: BackendConfig
    optimizer: OptimizerConfig
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    def to_dict(self) -> dict[str, Any]:
        """
        转成普通 dict，便于：
        - JSON/YAML 输出
        - CLI dump-config
        - 调试
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskConfig":
        """
        从普通 dict 构造 TaskConfig。
        主要供 YAML loader 使用。
        """
        if not isinstance(data, Mapping):
            raise TypeError(f"TaskConfig.from_dict expects a mapping, got {type(data).__name__}")

        meta_raw = data.get("meta", {})
        backend_raw = data.get("backend", {})
        optimizer_raw = data.get("optimizer", {})
        runtime_raw = data.get("runtime", {})

        if not isinstance(meta_raw, Mapping):
            raise TypeError("Field 'meta' must be a mapping/dict")
        if not isinstance(backend_raw, Mapping):
            raise TypeError("Field 'backend' must be a mapping/dict")
        if not isinstance(optimizer_raw, Mapping):
            raise TypeError("Field 'optimizer' must be a mapping/dict")
        if not isinstance(runtime_raw, Mapping):
            raise TypeError("Field 'runtime' must be a mapping/dict")

        return cls(
            meta=MetaConfig(**dict(meta_raw)),
            backend=BackendConfig(**dict(backend_raw)),
            optimizer=OptimizerConfig(**dict(optimizer_raw)),
            runtime=RuntimeConfig(**dict(runtime_raw)),
        )


# =============================================================================
# Convenience helpers
# =============================================================================
def task_config_from_dict(data: Mapping[str, Any]) -> TaskConfig:
    """
    便捷包装，供 loader.py 使用。
    """
    return TaskConfig.from_dict(data)


def validate_task_config(cfg: TaskConfig) -> TaskConfig:
    """
    对 TaskConfig 做基础校验。

    这里只做“结构层”的校验，不做过深的设备/物理语义校验。
    更细的校验（如 PV 重复、obj_math 合法性、策略名合法性）见 validators.py。
    """
    # -------------------------------------------------------------------------
    # meta
    # -------------------------------------------------------------------------
    if not cfg.meta.name or not str(cfg.meta.name).strip():
        raise ValueError("meta.name cannot be empty")

    # -------------------------------------------------------------------------
    # backend
    # -------------------------------------------------------------------------
    if not cfg.backend.type or not str(cfg.backend.type).strip():
        raise ValueError("backend.type cannot be empty")

    if cfg.backend.bounds_mode not in {"absolute", "relative"}:
        raise ValueError(
            f"backend.bounds_mode must be 'absolute' or 'relative', got {cfg.backend.bounds_mode!r}"
        )

    bounds = cfg.backend.bounds
    if not isinstance(bounds, list) or len(bounds) == 0:
        raise ValueError("backend.bounds must be a non-empty list")

    for i, item in enumerate(bounds):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"backend.bounds[{i}] must be a [low, high] pair")

        lo, hi = item
        try:
            lo = float(lo)
            hi = float(hi)
        except Exception as exc:
            raise ValueError(f"backend.bounds[{i}] must contain numeric values") from exc

        if not lo < hi:
            raise ValueError(f"backend.bounds[{i}] must satisfy low < high")

    if not isinstance(cfg.backend.kwargs, dict):
        raise TypeError("backend.kwargs must be a dict")

    # -------------------------------------------------------------------------
    # optimizer
    # -------------------------------------------------------------------------
    if not cfg.optimizer.name or not str(cfg.optimizer.name).strip():
        raise ValueError("optimizer.name cannot be empty")

    if not isinstance(cfg.optimizer.kwargs, dict):
        raise TypeError("optimizer.kwargs must be a dict")

    # -------------------------------------------------------------------------
    # runtime
    # -------------------------------------------------------------------------
    if not isinstance(cfg.runtime, RuntimeConfig):
        raise TypeError("runtime must be a RuntimeConfig instance")

    return cfg
