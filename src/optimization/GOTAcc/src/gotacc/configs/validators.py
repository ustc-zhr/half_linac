from __future__ import annotations

"""
Additional validators for GOTAcc TaskConfig.

设计目标
--------
schema.py 中的 validate_task_config() 只负责“基础结构校验”：
- 字段是否存在
- bounds 形状是否合法
- name/type 是否为空
- kwargs 是否为 dict

本文件负责“更细的语义校验”：
- backend.type 的专属字段是否合法
- EPICS 配置内部长度是否一致
- PV 是否重复
- policy 名字是否合法
- optimizer 与 combine_mode 是否匹配

建议使用方式
------------
在 loader.py 中先做：
    validate_task_config(cfg)
再做：
    validate_task_config_strict(cfg)

这样结构错误和语义错误都能尽早暴露。
"""

from collections import Counter
from typing import Any, Iterable, Sequence

from gotacc.configs.schema import TaskConfig


# =============================================================================
# public entry
# =============================================================================
def validate_task_config_strict(cfg: TaskConfig) -> TaskConfig:
    """
    对 TaskConfig 进行更严格的语义校验。

    校验内容
    --------
    1. backend.type 是否受支持
    2. optimizer.name 是否受支持
    3. EPICS backend 的字段完整性与长度一致性
    4. offline backend 的声明方式是否合法
    5. write_policy / objective_policy / combine_mode 合法性
    6. optimizer 与 combine_mode 是否匹配
    """
    _validate_backend_type(cfg)
    _validate_optimizer_name(cfg)
    _validate_backend_specific(cfg)
    _validate_optimizer_backend_match(cfg)
    return cfg


# =============================================================================
# common helpers
# =============================================================================
def _require_keys(d: dict[str, Any], keys: Sequence[str], what: str) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise KeyError(f"{what} is missing required keys: {missing}")


def _ensure_list_of_str(x: Any, field_name: str) -> list[str]:
    if not isinstance(x, list):
        raise TypeError(f"{field_name} must be a list")
    for i, item in enumerate(x):
        if not isinstance(item, str) or not item.strip():
            raise TypeError(f"{field_name}[{i}] must be a non-empty string")
    return x


def _ensure_duplicate_free(items: Sequence[str], field_name: str) -> None:
    counter = Counter(items)
    dup = [k for k, v in counter.items() if v > 1]
    if dup:
        raise ValueError(f"{field_name} contains duplicate entries: {dup}")


def _normalize_policy_names(x: Any, field_name: str) -> list[str]:
    if x is None:
        return []

    if isinstance(x, str):
        return [x]

    if isinstance(x, (list, tuple)):
        out = []
        for i, item in enumerate(x):
            if not isinstance(item, str):
                raise TypeError(f"{field_name}[{i}] must be a string")
            out.append(item)
        return out

    raise TypeError(f"{field_name} must be str / list / tuple / None")


# =============================================================================
# supported names
# =============================================================================
SUPPORTED_BACKEND_TYPES = {
    "epics",
    "offline",
}

SUPPORTED_SINGLE_OBJECTIVE_OPTIMIZERS = {
    "bo",
    "bayesopt",
    "bayesian_optimization",
    "consbo",
    "turbo",
    "trust_region_bo",
    "rcds",
    "smggpo",
    "mggpo_so",
    "single_objective_mggpo",
    "single_objective_mg-gpo",
    "consmggpo_so",
    "constrained_mggpo_so",
    "single_objective_consmggpo",
    "single_objective_constrained_mggpo",
    "single_objective_constrained_mg-gpo",
}

SUPPORTED_MULTI_OBJECTIVE_OPTIMIZERS = {
    "mobo",
    "consmobo",
    "consmggpo",
    "constrained_mggpo",
    "constrained_mg-gpo",
    "mggpo",
    "mopso",
    "nsga2",
    "nsga-ii",
}

SUPPORTED_OPTIMIZERS = (
    SUPPORTED_SINGLE_OBJECTIVE_OPTIMIZERS
    | SUPPORTED_MULTI_OBJECTIVE_OPTIMIZERS
)

SUPPORTED_COMBINE_MODES = {
    "weighted_sum",
    "vector",
}

SUPPORTED_WRITE_POLICIES = {
    "none",
    "equal",
    "xiaosesan_symmetry",
}

SUPPORTED_OBJECTIVE_POLICIES = {
    "fel_energy_guard",
    "zero_guard",
    "xiaosesan_zero_guard",
}

SUPPORTED_CONSTRAINT_POLICIES = {
    "bpm_guard",
    "bpm_zero_guard",
}

SUPPORTED_OBJ_MATH = {
    "mean",
    "std",
}


def _validate_policy_specs(
    specs: Any,
    *,
    field_name: str,
    supported_names: set[str],
) -> None:
    if specs is None:
        return
    if not isinstance(specs, list):
        raise TypeError(f"{field_name} must be a list")
    for i, spec in enumerate(specs):
        if not isinstance(spec, dict):
            raise TypeError(f"{field_name}[{i}] must be a dict")
        name = str(spec.get("name", "")).lower()
        if not name:
            raise ValueError(f"{field_name}[{i}] must define a non-empty 'name'")
        if name not in supported_names:
            raise ValueError(
                f"Unsupported {field_name}[{i}].name={name!r}. "
                f"Supported values: {sorted(supported_names)}"
            )
        kwargs = spec.get("kwargs", {})
        if not isinstance(kwargs, dict):
            raise TypeError(f"{field_name}[{i}]['kwargs'] must be a dict")


# =============================================================================
# top-level validators
# =============================================================================
def _validate_backend_type(cfg: TaskConfig) -> None:
    backend_type = str(cfg.backend.type).lower()
    if backend_type not in SUPPORTED_BACKEND_TYPES:
        raise ValueError(
            f"Unsupported backend.type={cfg.backend.type!r}. "
            f"Supported values: {sorted(SUPPORTED_BACKEND_TYPES)}"
        )


def _validate_optimizer_name(cfg: TaskConfig) -> None:
    optimizer_name = str(cfg.optimizer.name).lower()
    if optimizer_name not in SUPPORTED_OPTIMIZERS:
        raise ValueError(
            f"Unsupported optimizer.name={cfg.optimizer.name!r}. "
            f"Supported values: {sorted(SUPPORTED_OPTIMIZERS)}"
        )


def _validate_backend_specific(cfg: TaskConfig) -> None:
    backend_type = str(cfg.backend.type).lower()

    if backend_type == "epics":
        _validate_epics_backend(cfg)
        return

    if backend_type == "offline":
        _validate_offline_backend(cfg)
        return

    raise AssertionError(f"Unexpected backend type after validation: {backend_type!r}")


# =============================================================================
# EPICS backend validation
# =============================================================================
def _validate_epics_backend(cfg: TaskConfig) -> None:
    kwargs = cfg.backend.kwargs

    if not isinstance(kwargs, dict):
        raise TypeError("backend.kwargs must be a dict for EPICS backend")

    required_keys = [
        "knobs_pvnames",
        "obj_pvnames",
        "obj_weights",
        "obj_samples",
        "obj_math",
        "set_interval",
        "sample_interval",
        "combine_mode",
    ]
    _require_keys(kwargs, required_keys, what="EPICS backend.kwargs")

    knobs_pvnames = _ensure_list_of_str(kwargs["knobs_pvnames"], "backend.kwargs['knobs_pvnames']")
    obj_pvnames = _ensure_list_of_str(kwargs["obj_pvnames"], "backend.kwargs['obj_pvnames']")

    _ensure_duplicate_free(knobs_pvnames, "backend.kwargs['knobs_pvnames']")
    _ensure_duplicate_free(obj_pvnames, "backend.kwargs['obj_pvnames']")

    if "knob_readback_pvnames" in kwargs and kwargs["knob_readback_pvnames"] is not None:
        knob_readback_pvnames = _ensure_list_of_str(
            kwargs["knob_readback_pvnames"],
            "backend.kwargs['knob_readback_pvnames']",
        )
        if len(knob_readback_pvnames) != len(knobs_pvnames):
            raise ValueError(
                f"len(knob_readback_pvnames) ({len(knob_readback_pvnames)}) must match "
                f"len(knobs_pvnames) ({len(knobs_pvnames)})"
            )

    # bounds 维度必须和 knobs 数量一致
    if len(cfg.backend.bounds) != len(knobs_pvnames):
        raise ValueError(
            f"backend.bounds dim ({len(cfg.backend.bounds)}) must match "
            f"len(knobs_pvnames) ({len(knobs_pvnames)})"
        )

    # obj_weights
    obj_weights = kwargs["obj_weights"]
    if not isinstance(obj_weights, list):
        raise TypeError("backend.kwargs['obj_weights'] must be a list")
    if len(obj_weights) != len(obj_pvnames):
        raise ValueError(
            f"len(obj_weights) ({len(obj_weights)}) must match "
            f"len(obj_pvnames) ({len(obj_pvnames)})"
        )
    for i, w in enumerate(obj_weights):
        try:
            float(w)
        except Exception as exc:
            raise TypeError(f"obj_weights[{i}] must be numeric") from exc

    # obj_samples
    obj_samples = kwargs["obj_samples"]
    if not isinstance(obj_samples, int):
        raise TypeError("backend.kwargs['obj_samples'] must be an int")
    if obj_samples < 1:
        raise ValueError("backend.kwargs['obj_samples'] must be >= 1")

    # obj_math
    obj_math = kwargs["obj_math"]
    if not isinstance(obj_math, list):
        raise TypeError("backend.kwargs['obj_math'] must be a list")
    if len(obj_math) != len(obj_pvnames):
        raise ValueError(
            f"len(obj_math) ({len(obj_math)}) must match "
            f"len(obj_pvnames) ({len(obj_pvnames)})"
        )
    for i, op in enumerate(obj_math):
        if op not in SUPPORTED_OBJ_MATH:
            raise ValueError(
                f"Unsupported obj_math[{i}]={op!r}. "
                f"Supported values: {sorted(SUPPORTED_OBJ_MATH)}"
            )

    constraint_pvnames = kwargs.get("constraint_pvnames", [])
    if constraint_pvnames is None:
        constraint_pvnames = []
    else:
        constraint_pvnames = _ensure_list_of_str(
            constraint_pvnames,
            "backend.kwargs['constraint_pvnames']",
        )

    constraint_math = kwargs.get("constraint_math", [])
    if not isinstance(constraint_math, list):
        raise TypeError("backend.kwargs['constraint_math'] must be a list")
    if len(constraint_math) != len(constraint_pvnames):
        raise ValueError(
            f"len(constraint_math) ({len(constraint_math)}) must match "
            f"len(constraint_pvnames) ({len(constraint_pvnames)})"
        )
    for i, op in enumerate(constraint_math):
        if op not in SUPPORTED_OBJ_MATH:
            raise ValueError(
                f"Unsupported constraint_math[{i}]={op!r}. "
                f"Supported values: {sorted(SUPPORTED_OBJ_MATH)}"
            )

    if "constraint_bounds" in kwargs and kwargs["constraint_bounds"] is not None:
        constraint_bounds = kwargs["constraint_bounds"]
        if not isinstance(constraint_bounds, list):
            raise TypeError("backend.kwargs['constraint_bounds'] must be a list")
        if len(constraint_bounds) != len(constraint_pvnames):
            raise ValueError(
                f"len(constraint_bounds) ({len(constraint_bounds)}) must match "
                f"len(constraint_pvnames) ({len(constraint_pvnames)})"
            )
        for i, bound in enumerate(constraint_bounds):
            if not isinstance(bound, (list, tuple)) or len(bound) != 2:
                raise TypeError(
                    f"backend.kwargs['constraint_bounds'][{i}] must be a (lower, upper) pair"
                )
            lower, upper = bound
            if lower is not None:
                try:
                    float(lower)
                except Exception as exc:
                    raise TypeError(
                        f"backend.kwargs['constraint_bounds'][{i}][0] must be numeric or None"
                    ) from exc
            if upper is not None:
                try:
                    float(upper)
                except Exception as exc:
                    raise TypeError(
                        f"backend.kwargs['constraint_bounds'][{i}][1] must be numeric or None"
                    ) from exc
            if lower is not None and upper is not None and float(lower) > float(upper):
                raise ValueError(
                    f"backend.kwargs['constraint_bounds'][{i}] must satisfy lower <= upper"
                )

    # set_interval / sample_interval
    for key in ("set_interval", "sample_interval"):
        interval = kwargs[key]
        try:
            interval = float(interval)
        except Exception as exc:
            raise TypeError(f"backend.kwargs['{key}'] must be numeric") from exc
        if interval < 0:
            raise ValueError(f"backend.kwargs['{key}'] must be >= 0")

    # combine_mode
    combine_mode = str(kwargs["combine_mode"]).lower()
    if combine_mode not in SUPPORTED_COMBINE_MODES:
        raise ValueError(
            f"Unsupported combine_mode={kwargs['combine_mode']!r}. "
            f"Supported values: {sorted(SUPPORTED_COMBINE_MODES)}"
        )

    # readback_tol 若提供，需是标量或与 knobs 数量一致的数组
    if "readback_tol" in kwargs and kwargs["readback_tol"] is not None:
        tol = kwargs["readback_tol"]
        if isinstance(tol, (int, float)):
            pass
        elif isinstance(tol, list):
            if len(tol) != len(knobs_pvnames):
                raise ValueError(
                    f"readback_tol list length ({len(tol)}) must match "
                    f"len(knobs_pvnames) ({len(knobs_pvnames)})"
                )
            for i, v in enumerate(tol):
                try:
                    float(v)
                except Exception as exc:
                    raise TypeError(f"readback_tol[{i}] must be numeric") from exc
        else:
            raise TypeError("readback_tol must be numeric, list, or None")

    # write_policy
    write_policy = kwargs.get("write_policy", None)
    if write_policy is not None:
        if str(write_policy).lower() not in SUPPORTED_WRITE_POLICIES:
            raise ValueError(
                f"Unsupported write_policy={write_policy!r}. "
                f"Supported values: {sorted(SUPPORTED_WRITE_POLICIES)}"
            )

    # objective_policy
    objective_policy_names = _normalize_policy_names(
        kwargs.get("objective_policy", None),
        "backend.kwargs['objective_policy']",
    )
    for name in objective_policy_names:
        if str(name).lower() not in SUPPORTED_OBJECTIVE_POLICIES:
            raise ValueError(
                f"Unsupported objective_policy={name!r}. "
                f"Supported values: {sorted(SUPPORTED_OBJECTIVE_POLICIES)}"
            )

    _validate_policy_specs(
        kwargs.get("objective_policies", None),
        field_name="backend.kwargs['objective_policies']",
        supported_names=SUPPORTED_OBJECTIVE_POLICIES,
    )

    constraint_policy_names = _normalize_policy_names(
        kwargs.get("constraint_policy", None),
        "backend.kwargs['constraint_policy']",
    )
    for name in constraint_policy_names:
        if str(name).lower() not in SUPPORTED_CONSTRAINT_POLICIES:
            raise ValueError(
                f"Unsupported constraint_policy={name!r}. "
                f"Supported values: {sorted(SUPPORTED_CONSTRAINT_POLICIES)}"
            )

    _validate_policy_specs(
        kwargs.get("constraint_policies", None),
        field_name="backend.kwargs['constraint_policies']",
        supported_names=SUPPORTED_CONSTRAINT_POLICIES,
    )


# =============================================================================
# offline backend validation
# =============================================================================
def _validate_offline_backend(cfg: TaskConfig) -> None:
    kwargs = cfg.backend.kwargs

    if not isinstance(kwargs, dict):
        raise TypeError("backend.kwargs must be a dict for offline backend")

    has_func = "func" in kwargs
    has_callable_path = "callable_path" in kwargs

    if has_func and has_callable_path:
        raise ValueError(
            "Offline backend.kwargs cannot contain both 'func' and 'callable_path'"
        )

    if not has_func and not has_callable_path:
        raise ValueError(
            "Offline backend requires either backend.kwargs['func'] "
            "or backend.kwargs['callable_path']"
        )

    if has_func and not callable(kwargs["func"]):
        raise TypeError("backend.kwargs['func'] must be callable")

    if has_callable_path and not isinstance(kwargs["callable_path"], str):
        raise TypeError("backend.kwargs['callable_path'] must be a string")

    # 若 relative bounds，则建议/要求提供 x0
    if cfg.backend.bounds_mode == "relative":
        if "x0" not in kwargs and "initial_point" not in kwargs:
            raise ValueError(
                "Offline backend with bounds_mode='relative' requires "
                "backend.kwargs['x0'] or backend.kwargs['initial_point']"
            )


# =============================================================================
# optimizer / backend consistency
# =============================================================================
def _validate_optimizer_backend_match(cfg: TaskConfig) -> None:
    optimizer_name = str(cfg.optimizer.name).lower()

    # offline backend 可能没有 combine_mode，这里只对 epics 强约束
    combine_mode = str(cfg.backend.kwargs.get("combine_mode", "weighted_sum")).lower()

    if optimizer_name in SUPPORTED_SINGLE_OBJECTIVE_OPTIMIZERS:
        if combine_mode != "weighted_sum":
            raise ValueError(
                f"Single-objective optimizer {cfg.optimizer.name!r} requires "
                f"combine_mode='weighted_sum', got {combine_mode!r}"
            )

    if optimizer_name in SUPPORTED_MULTI_OBJECTIVE_OPTIMIZERS:
        if combine_mode != "vector":
            raise ValueError(
                f"Multi-objective optimizer {cfg.optimizer.name!r} requires "
                f"combine_mode='vector', got {combine_mode!r}"
            )
