from __future__ import annotations

"""
Backend factory for GOTAcc.

当前只保留两条主链：
    1. EPICS backend
    2. offline backend

设计原则
--------
1. 不再支持 class_path 这种过渡期动态入口
2. backend 的选择只由 backend.type 决定
3. policy 由 backend.kwargs 中的显式字段控制
4. offline 保留最小可用封装，方便 benchmark / test function / 仿真目标
"""

import importlib
from typing import Any, Callable, Sequence

import numpy as np

from gotacc.configs.schema import TaskConfig
from gotacc.interfaces.base import ObjectiveBackend
from gotacc.interfaces.epics import (
    BPMGuardConstraintPolicy,
    BaseConstraintPolicy,
    BaseObjectivePolicy,
    BaseWritePolicy,
    CompositeConstraintPolicy,
    CompositeObjectivePolicy,
    EpicsObjective,
    FelEnergyGuardPolicy,
    EqualWritePolicy,
    ZeroGuardPolicy,
)


# =============================================================================
# small utility
# =============================================================================
def import_string(path: str) -> Any:
    """
    Import object from a dotted path.

    支持两种写法：
        "package.module:object"
        "package.module.object"

    当前主要用于 offline backend 的 callable_path。
    """
    if ":" in path:
        module_name, attr_name = path.split(":", 1)
    else:
        module_name, attr_name = path.rsplit(".", 1)

    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


# =============================================================================
# offline backend: minimal callable wrapper
# =============================================================================
class CallableObjectiveBackend(ObjectiveBackend):
    """
    用普通 Python callable 构造一个最小 offline backend。

    适用场景
    --------
    - benchmark function
    - 离线测试函数
    - 简单仿真目标
    """

    def __init__(
        self,
        func: Callable[[np.ndarray], Any],
        x0: np.ndarray | Sequence[float] | None = None,
    ) -> None:
        self.func = func
        self._x0 = None if x0 is None else np.asarray(x0, dtype=float)

    def init_knob_value(self) -> np.ndarray:
        """
        返回 offline 初始点。

        注意：
        若 backend.bounds_mode == "relative"，则必须提供 x0。
        若 bounds_mode == "absolute"，runner 即便不用 x0，也可能会调用这里；
        所以建议 offline 任务尽量都提供 x0，减少歧义。
        """
        if self._x0 is None:
            raise ValueError(
                "Offline backend requires x0 when initial point is needed."
            )
        return self._x0.copy()

    def evaluate(self, x: np.ndarray | Sequence[float]) -> Any:
        x = np.asarray(x, dtype=float)
        return self.func(x)

    def evaluate_func(self, x: np.ndarray | Sequence[float]) -> Any:
        """
        兼容某些地方仍可能调用 evaluate_func() 的习惯。
        """
        return self.evaluate(x)


# =============================================================================
# write policy builder
# =============================================================================
def build_write_policy(
    name: str | None,
    kwargs: dict[str, Any] | None = None,
) -> BaseWritePolicy | None:
    """
    根据字符串名字构造写入策略。

    当前内置支持：
        None / "none"        -> 不启用特殊写入策略
        "equal"              -> 让某个指定pv与某指定knob相等
    """
    if kwargs is None:
        kwargs = {}

    if name is None:
        return None

    name = str(name).lower()

    if name in {"none"}:
        return None

    if name in {"equal", "xiaosesan_symmetry"}:
        return EqualWritePolicy(
            extra_links=kwargs.get("pvlinks", None),
        )

    raise ValueError(f"Unknown write policy: {name!r}")


# =============================================================================
# objective policy builder
# =============================================================================
def _normalize_policy_names(x: str | Sequence[str] | None) -> list[str]:
    """
    把 objective_policy 配置统一转成字符串列表。
    """
    if x is None:
        return []

    if isinstance(x, str):
        return [x]

    if isinstance(x, (list, tuple)):
        return [str(v) for v in x]

    raise TypeError(
        f"objective_policy must be str / list / tuple / None, got {type(x).__name__}"
    )

def _build_one_objective_policy(name: str, kwargs: dict[str, Any]) -> BaseObjectivePolicy:
    lname = str(name).lower()

    if lname == "fel_energy_guard":
        return FelEnergyGuardPolicy(
            target_col=kwargs.get("target_col", 0),
            large_threshold=kwargs.get("large_threshold", 1e6),
            change_threshold=kwargs.get("change_threshold", 1e-6),
        )

    if lname in {"zero_guard", "xiaosesan_zero_guard"}:
        return ZeroGuardPolicy(
            target_col=kwargs.get("target_col", 1),
            zero_atol=kwargs.get("zero_atol", 1e-12),
            offset=kwargs.get("offset", 100.0),
        )

    raise ValueError(f"Unknown objective policy: {name!r}")

def build_objective_policies(policy_specs):
    if policy_specs is None:
        return None

    if not isinstance(policy_specs, list):
        raise TypeError("objective_policies must be a list")

    built = []
    for i, spec in enumerate(policy_specs):
        if not isinstance(spec, dict):
            raise TypeError(f"objective_policies[{i}] must be a dict")

        name = spec.get("name")
        kwargs = spec.get("kwargs", {})

        if not isinstance(kwargs, dict):
            raise TypeError(f"objective_policies[{i}]['kwargs'] must be a dict")

        built.append(_build_one_objective_policy(name, kwargs))

    if len(built) == 0:
        return None
    if len(built) == 1:
        return built[0]
    return CompositeObjectivePolicy(built)


# =============================================================================
# constraint policy builder
# =============================================================================
def _build_one_constraint_policy(name: str, kwargs: dict[str, Any]) -> BaseConstraintPolicy:
    lname = str(name).lower()

    if lname in {"bpm_guard", "bpm_zero_guard"}:
        return BPMGuardConstraintPolicy(
            target_col=kwargs.get("target_col", 0),
            zero_atol=kwargs.get("zero_atol", 1e-9),
            delta_ratio=kwargs.get("delta_ratio", 0.1),
            delta_min=kwargs.get("delta_min", 1e-6),
            scale_floor=kwargs.get("scale_floor", 1.0),
        )

    raise ValueError(f"Unknown constraint policy: {name!r}")


def build_constraint_policies(policy_specs):
    if policy_specs is None:
        return None

    if not isinstance(policy_specs, list):
        raise TypeError("constraint_policies must be a list")

    built = []
    for i, spec in enumerate(policy_specs):
        if not isinstance(spec, dict):
            raise TypeError(f"constraint_policies[{i}] must be a dict")

        name = spec.get("name")
        kwargs = spec.get("kwargs", {})

        if not isinstance(kwargs, dict):
            raise TypeError(f"constraint_policies[{i}]['kwargs'] must be a dict")

        built.append(_build_one_constraint_policy(name, kwargs))

    if len(built) == 0:
        return None
    if len(built) == 1:
        return built[0]
    return CompositeConstraintPolicy(built)


def build_constraint_policy(
    names: str | Sequence[str] | None,
    kwargs: dict[str, Any] | None = None,
) -> BaseConstraintPolicy | None:
    """
    根据字符串名字构造约束策略。

    当前内置支持：
        None / "none"
        "bpm_guard"
        "bpm_zero_guard"
    """
    if kwargs is None:
        kwargs = {}

    name_list = _normalize_policy_names(names)
    if len(name_list) == 0:
        return None

    built: list[BaseConstraintPolicy] = []

    for name in name_list:
        lname = str(name).lower()

        if lname in {"none"}:
            continue

        if lname in {"bpm_guard", "bpm_zero_guard"}:
            built.append(
                BPMGuardConstraintPolicy(
                    target_col=kwargs.get("target_col", 0),
                    zero_atol=kwargs.get("zero_atol", 1e-9),
                    delta_ratio=kwargs.get("delta_ratio", 0.1),
                    delta_min=kwargs.get("delta_min", 1e-6),
                    scale_floor=kwargs.get("scale_floor", 1.0),
                )
            )
            continue

        raise ValueError(f"Unknown constraint policy: {name!r}")

    if len(built) == 0:
        return None

    if len(built) == 1:
        return built[0]

    return CompositeConstraintPolicy(built)

def build_objective_policy(
    names: str | Sequence[str] | None,
    kwargs: dict[str, Any] | None = None,
) -> BaseObjectivePolicy | None:
    """
    根据字符串名字构造目标策略。

    当前内置支持：
        None / "none"
        "fel_energy_guard"
        "zero_guard"

    若 names 是列表，则按顺序组合成 CompositeObjectivePolicy。
    """
    if kwargs is None:
        kwargs = {}

    name_list = _normalize_policy_names(names)
    if len(name_list) == 0:
        return None

    built: list[BaseObjectivePolicy] = []

    for name in name_list:
        lname = str(name).lower()

        if lname in {"none"}:
            continue

        if lname == "fel_energy_guard":
            built.append(
                FelEnergyGuardPolicy(
                    target_col=kwargs.get("target_col", 0),
                    large_threshold=kwargs.get("large_threshold", 1e6),
                    change_threshold=kwargs.get("change_threshold", 1e-6),
                )
            )
            continue

        if lname in {"zero_guard", "xiaosesan_zero_guard"}:
            built.append(
                ZeroGuardPolicy(
                    target_col=kwargs.get("target_col", 1),
                    zero_atol=kwargs.get("zero_atol", 1e-12),
                    offset=kwargs.get("offset", 100.0),
                )
            )
            continue

        raise ValueError(f"Unknown objective policy: {name!r}")

    if len(built) == 0:
        return None

    if len(built) == 1:
        return built[0]

    return CompositeObjectivePolicy(built)


# =============================================================================
# main factory entry
# =============================================================================
def build_backend(task_cfg: TaskConfig) -> ObjectiveBackend:
    """
    对外统一入口。

    当前只支持：
        backend.type == "epics"
        backend.type == "offline"
    """
    backend_type = str(task_cfg.backend.type).lower()

    if backend_type == "epics":
        return _build_epics_backend(task_cfg)

    if backend_type == "offline":
        return _build_offline_backend(task_cfg)

    raise ValueError(
        f"Unknown backend type: {task_cfg.backend.type!r}. "
        "Supported backend types are: 'epics', 'offline'."
    )


# =============================================================================
# epics builder
# =============================================================================
def _build_epics_backend(task_cfg: TaskConfig) -> ObjectiveBackend:
    """
    构造统一的 EPICS backend。

    约定：
    ----
    由 task_cfg.backend.kwargs 显式提供以下字段：

    必需字段：
        knobs_pvnames
        obj_pvnames
        obj_weights
        obj_samples
        obj_math
        set_interval
        sample_interval

    常用可选字段：
        log_path
        readback_check
        readback_tol
        combine_mode
        write_policy
        write_policy_kwargs
        objective_policy
        objective_policy_kwargs
        best_selector_mode
    """
    kwargs = dict(task_cfg.backend.kwargs)

    # -------------------------------------------------------------------------
    # policy config
    # -------------------------------------------------------------------------
    write_policy_name = kwargs.pop("write_policy", None)
    write_policy_kwargs = kwargs.pop("write_policy_kwargs", {})
    write_policy = build_write_policy(write_policy_name, write_policy_kwargs)

    objective_policy_specs = kwargs.pop("objective_policies", None)

    if objective_policy_specs is not None:
        objective_policy = build_objective_policies(objective_policy_specs)
    else:
        objective_policy_names = kwargs.pop("objective_policy", None)
        objective_policy_kwargs = kwargs.pop("objective_policy_kwargs", {})
        objective_policy = build_objective_policy(objective_policy_names, objective_policy_kwargs)

    constraint_policy_specs = kwargs.pop("constraint_policies", None)

    if constraint_policy_specs is not None:
        constraint_policy = build_constraint_policies(constraint_policy_specs)
    else:
        constraint_policy_names = kwargs.pop("constraint_policy", None)
        constraint_policy_kwargs = kwargs.pop("constraint_policy_kwargs", {})
        constraint_policy = build_constraint_policy(constraint_policy_names, constraint_policy_kwargs)

    # -------------------------------------------------------------------------
    # backend core config
    # -------------------------------------------------------------------------
    try:
        knobs_pvnames = kwargs.pop("knobs_pvnames")
        knob_readback_pvnames = kwargs.pop("knob_readback_pvnames", None)
        obj_pvnames = kwargs.pop("obj_pvnames")
        obj_weights = kwargs.pop("obj_weights")
        obj_samples = kwargs.pop("obj_samples")
        obj_math = kwargs.pop("obj_math")
        constraint_pvnames = kwargs.pop("constraint_pvnames", [])
        constraint_math = kwargs.pop("constraint_math", [])
        constraint_bounds = kwargs.pop("constraint_bounds", [])
        set_interval = kwargs.pop("set_interval")
        sample_interval = kwargs.pop("sample_interval")
    except KeyError as exc:
        raise KeyError(
            f"Missing required EPICS backend kwarg: {exc.args[0]!r}"
        ) from exc

    log_path = kwargs.pop("log_path", "template.opt")
    readback_check = kwargs.pop("readback_check", False)
    readback_tol = kwargs.pop("readback_tol", None)
    combine_mode = kwargs.pop("combine_mode", "weighted_sum")
    best_selector_mode = kwargs.pop("best_selector_mode", None)

    backend = EpicsObjective(
        knobs_pvnames=knobs_pvnames,
        knob_readback_pvnames=knob_readback_pvnames,
        obj_pvnames=obj_pvnames,
        obj_weights=obj_weights,
        obj_samples=obj_samples,
        obj_math=obj_math,
        constraint_pvnames=constraint_pvnames,
        constraint_math=constraint_math,
        constraint_bounds=constraint_bounds,
        set_interval=set_interval,
        sample_interval=sample_interval,
        log_path=log_path,
        readback_check=readback_check,
        readback_tol=readback_tol,
        combine_mode=combine_mode,
        write_policy=write_policy,
        objective_policy=objective_policy,
        constraint_policy=constraint_policy,
        best_selector_mode=best_selector_mode,
    )

    # 若还有未消费字段，直接报错，避免“悄悄写错字段名但没人发现”
    if len(kwargs) > 0:
        raise ValueError(
            f"Unused EPICS backend kwargs: {list(kwargs.keys())}"
        )

    return backend


# =============================================================================
# offline builder
# =============================================================================
def _build_offline_backend(task_cfg: TaskConfig) -> ObjectiveBackend:
    """
    构造 offline backend。

    当前只支持两种声明方式：
    1. backend.kwargs["func"] 是直接传入的 Python callable
    2. backend.kwargs["callable_path"] 是函数导入路径

    可选字段：
        x0
        initial_point   （与 x0 等价，二选一）
    """
    kwargs = dict(task_cfg.backend.kwargs)

    # 1) 直接给 callable
    if "func" in kwargs:
        func = kwargs.pop("func")
        if not callable(func):
            raise TypeError("backend.kwargs['func'] must be callable")

        x0 = kwargs.pop("x0", kwargs.pop("initial_point", None))

        if len(kwargs) > 0:
            raise ValueError(
                f"Unused offline backend kwargs: {list(kwargs.keys())}"
            )

        return CallableObjectiveBackend(func=func, x0=x0)

    # 2) 给 callable_path
    if "callable_path" in kwargs:
        callable_path = kwargs.pop("callable_path")
        func = import_string(callable_path)

        if not callable(func):
            raise TypeError(
                f"Imported object from {callable_path!r} is not callable"
            )

        x0 = kwargs.pop("x0", kwargs.pop("initial_point", None))

        if len(kwargs) > 0:
            raise ValueError(
                f"Unused offline backend kwargs: {list(kwargs.keys())}"
            )

        return CallableObjectiveBackend(func=func, x0=x0)

    raise ValueError(
        "Offline backend requires either:\n"
        "  - backend.kwargs['func']\n"
        "  - backend.kwargs['callable_path']"
    )
