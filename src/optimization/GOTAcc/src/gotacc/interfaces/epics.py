from __future__ import annotations

"""
通用 EPICS backend（带 policy / hook 机制）

设计目标
--------
这份文件的目标不是“一次性吞掉所有历史特殊逻辑”，而是：

1. 已经较稳定的通用能力：
   - 读取/写入 PV
   - 多次采样
   - 统计聚合（mean/std）
   - readback 检查
   - 记录日志
   - 恢复初始值
   - set_best()

2. 真实装置测试中的特殊行为，拆成“可插拔 policy”：
   - FEL 能量优化中的异常值保护
   - 消色散问题中的对称四极联动写入
   - 惩罚：比如消色散问题中的 obj_vals[1] == 0 时整体加 100

3. 为后续 CLI / config / factory 统一装配创造条件。
"""

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from gotacc.interfaces.base import ObjectiveBackend


# =============================================================================
# EPICS 延迟导入
# =============================================================================
def _load_epics():
    """
    延迟导入 pyepics。
    """
    try:
        from epics import caget, caput, caput_many, caget_many
    except ImportError as exc:
        raise ImportError(
            "EPICS backend requires pyepics. "
            'Please install it first, e.g. pip install ".[epics]" '
            "or pip install pyepics"
        ) from exc

    return caget, caput, caput_many, caget_many


# =============================================================================
# Write policy：负责“如何把 x 写到机器上”
# =============================================================================
class BaseWritePolicy:
    """
    写入策略基类。

    默认情况下，只需要：
        caput_many(knobs_pvnames, x)
    """

    def apply(self, backend: "EpicsObjective", x: np.ndarray) -> None:
        raise NotImplementedError


class DefaultWritePolicy(BaseWritePolicy):
    """
    默认写入策略：仅把 x 逐项写到 knobs_pvnames。
    """

    def apply(self, backend: "EpicsObjective", x: np.ndarray) -> None:
        backend._caput_many(backend.knobs_pvnames, x.tolist())


class EqualWritePolicy(BaseWritePolicy):
    """
    让某些pv和knob中的某些值相等。

    如IRFEL消色散问题中：
        QM15 <- x[11]
        QM16 <- x[10]

    这里把这件事抽成一个策略类。

    参数
    ----
    extra_links:
        额外联动写入规则列表，每个元素是:
            (source_index_in_x, target_pvname)
    """

    def __init__(
        self,
        extra_links: Sequence[tuple[int, str]] | None = None,
    ) -> None:
        
        self.extra_links = list(extra_links) if extra_links is not None else []

    def apply(self, backend: "EpicsObjective", x: np.ndarray) -> None:
        # 先按常规把优化变量对应的 knobs 写入
        backend._caput_many(backend.knobs_pvnames, x.tolist())

        # 再执行额外联动写入
        for src_idx, target_pv in self.extra_links:
            if src_idx < 0 or src_idx >= len(x):
                raise IndexError(
                    f"EqualWritePolicy: source index {src_idx} out of range "
                    f"for x of length {len(x)}"
                )
            backend._caput(target_pv, float(x[src_idx]))


# =============================================================================
# Objective policy：负责“目标采样后的特殊处理”
# =============================================================================
class BaseObjectivePolicy:
    """
    目标策略基类。

    一个目标策略可以在三个阶段做事：

    1. preprocess_total():
       原始采样矩阵 total，shape = (obj_samples, n_obj)
       适合做原始采样层面的预清洗。

    2. post_reduce():
       total 经 mean/std 聚合后得到 results，shape = (n_obj,)
       适合做某些目标的修正、屏蔽、替换等。

    3. post_combine():
       把 results 进一步组合为最终优化器看到的 y 后，再做最后修正。
       例如：
         - weighted_sum 模式下得到一个标量
         - vector 模式下得到一个向量
    """

    def preprocess_total(
        self,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray:
        return total

    def post_reduce(
        self,
        results: np.ndarray,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray:
        return results

    def post_combine(
        self,
        y: np.ndarray | float,
        results: np.ndarray,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray | float:
        return y

    def select_best_index(
        self,
        y_history: np.ndarray,
        backend: "EpicsObjective",
    ) -> int | None:
        """
        可选：若某类问题想自己定义“如何从历史记录中选 best”，
        可以在这里返回 best index。

        返回 None 表示交回 backend 的默认逻辑处理。
        """
        return None

    def validate_backend(self, backend: "EpicsObjective") -> None:
        """Validate policy/backend compatibility before any evaluation writes."""
        return None

    def set_event_sink(self, sink: Callable[[str], None] | None) -> None:
        """Attach an optional runtime event sink without changing policy behavior."""
        self._event_sink = sink

    def emit_event(self, message: str) -> None:
        sink = getattr(self, "_event_sink", None)
        if sink is not None:
            sink(str(message))


class CompositeObjectivePolicy(BaseObjectivePolicy):
    """
    组合多个目标策略，按顺序串行执行。

    这样以后如果你想同时挂多个小策略，不需要再写一个大而杂的类。
    """

    def __init__(self, policies: Sequence[BaseObjectivePolicy]) -> None:
        self.policies = list(policies)

    def preprocess_total(self, total: np.ndarray, backend: "EpicsObjective") -> np.ndarray:
        for p in self.policies:
            total = p.preprocess_total(total, backend)
        return total

    def post_reduce(
        self,
        results: np.ndarray,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray:
        for p in self.policies:
            results = p.post_reduce(results, total, backend)
        return results

    def post_combine(
        self,
        y: np.ndarray | float,
        results: np.ndarray,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray | float:
        for p in self.policies:
            y = p.post_combine(y, results, total, backend)
        return y

    def select_best_index(self, y_history: np.ndarray, backend: "EpicsObjective") -> int | None:
        for p in self.policies:
            idx = p.select_best_index(y_history, backend)
            if idx is not None:
                return idx
        return None

    def validate_backend(self, backend: "EpicsObjective") -> None:
        for policy in self.policies:
            policy.validate_backend(backend)

    def set_event_sink(self, sink: Callable[[str], None] | None) -> None:
        for policy in self.policies:
            policy.set_event_sink(sink)


class FelEnergyGuardPolicy(BaseObjectivePolicy):
    """
    FEL 单目标优化中的异常保护策略。

    FEL 能量 interface 中的特殊处理：
    对第 1 个目标（即 results[0] 对应的那一列）进行保护判断：
      1) 若其均值绝对值过大
      2) 或这一列几乎没有变化
    则把该目标直接置为 0。

    这类逻辑的动机是：
    某些 FEL 输出信号异常时，会出现非常大的假信号，或者完全不变化，
    用它直接参与优化会把 BO/TuRBO 带偏。

    参数
    ----
    target_col:
        要检查的目标列索引，默认 0（与你旧脚本一致）

    large_threshold:
        “异常大”的阈值

    change_threshold:
        “几乎不变化”的阈值（用极差判断）
    """

    def __init__(
        self,
        target_col: int = 0,
        large_threshold: float = 1e6,
        change_threshold: float = 1e-6,
    ) -> None:
        self.target_col = int(target_col)
        self.large_threshold = float(large_threshold)
        self.change_threshold = float(change_threshold)

    def post_reduce(
        self,
        results: np.ndarray,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray:
        results = np.asarray(results, dtype=float).copy()

        if total.ndim != 2:
            return results
        if self.target_col < 0 or self.target_col >= total.shape[1]:
            return results

        col = np.asarray(total[:, self.target_col], dtype=float)

        too_large = np.mean(np.abs(col)) > self.large_threshold
        almost_no_change = (np.max(col) - np.min(col)) < self.change_threshold

        if too_large or almost_no_change:
            results[self.target_col] = 0.0

        return results


class ZeroGuardPolicy(BaseObjectivePolicy):
    """
    对指定目标列做零值保护。

    若 results[target_col] 接近 0，则直接在该目标列上加 offset。
    这样策略作用于 post_reduce 阶段，与 weighted_sum / vector 无关。
    """

    def __init__(
        self,
        target_col: int = 0,
        zero_atol: float = 1e-12,
        offset: float = 100.0,
    ) -> None:
        self.target_col = int(target_col)
        self.zero_atol = float(zero_atol)
        self.offset = float(offset)

    def post_reduce(
        self,
        results: np.ndarray,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray:
        results = np.asarray(results, dtype=float).copy()

        if results.ndim != 1:
            return results
        if self.target_col < 0 or self.target_col >= len(results):
            return results

        if np.isclose(results[self.target_col], 0.0, atol=self.zero_atol):
            results[self.target_col] = results[self.target_col] + self.offset

        return results


# =============================================================================
# Constraint policy：负责“约束采样后的特殊处理”
# =============================================================================
class BaseConstraintPolicy:
    """
    约束策略基类。

    一个约束策略可以在两个阶段做事：

    1. preprocess_total():
       原始约束采样矩阵 total，shape = (obj_samples, n_constraints)
       适合做原始采样层面的预清洗。

    2. post_reduce():
       total 经 mean/std 聚合后得到 results，shape = (n_constraints,)
       适合做约束修正、哨兵值替换等。
    """

    def preprocess_total(
        self,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray:
        return total

    def post_reduce(
        self,
        results: np.ndarray,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray:
        return results

    def validate_backend(self, backend: "EpicsObjective") -> None:
        """Validate policy/backend compatibility before any evaluation writes."""
        return None

    def set_event_sink(self, sink: Callable[[str], None] | None) -> None:
        """Attach an optional runtime event sink without changing policy behavior."""
        self._event_sink = sink

    def emit_event(self, message: str) -> None:
        sink = getattr(self, "_event_sink", None)
        if sink is not None:
            sink(str(message))


class CompositeConstraintPolicy(BaseConstraintPolicy):
    """
    组合多个约束策略，按顺序串行执行。
    """

    def __init__(self, policies: Sequence[BaseConstraintPolicy]) -> None:
        self.policies = list(policies)

    def preprocess_total(self, total: np.ndarray, backend: "EpicsObjective") -> np.ndarray:
        for p in self.policies:
            total = p.preprocess_total(total, backend)
        return total

    def post_reduce(
        self,
        results: np.ndarray,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray:
        for p in self.policies:
            results = p.post_reduce(results, total, backend)
        return results

    def validate_backend(self, backend: "EpicsObjective") -> None:
        for policy in self.policies:
            policy.validate_backend(backend)

    def set_event_sink(self, sink: Callable[[str], None] | None) -> None:
        for policy in self.policies:
            policy.set_event_sink(sink)


class BPMGuardConstraintPolicy(BaseConstraintPolicy):
    """
    BPM 约束零值保护。

    若某个约束列在一次 evaluate 的所有 sample 中都“几乎为 0”，
    则认为这个约束读数不是正式物理值，而是用“刚刚越界”的 sentinel
    替换 reduce 后的约束值。

    sentinel 规则：
      - 若该 constraint 有 upper bound，则返回 ub + delta
      - 否则若有 lower bound，则返回 lb - delta

    delta 规则：
      - 双边约束：max(delta_min, delta_ratio * (ub - lb))
      - 单边约束：max(delta_min, delta_ratio * max(abs(bound), scale_floor))
    """

    def __init__(
        self,
        target_col: int = 0,
        zero_atol: float = 1e-9,
        delta_ratio: float = 0.1,
        delta_min: float = 1e-6,
        scale_floor: float = 1.0,
    ) -> None:
        self.target_col = int(target_col)
        self.zero_atol = float(zero_atol)
        self.delta_ratio = float(delta_ratio)
        self.delta_min = float(delta_min)
        self.scale_floor = float(scale_floor)

    def _sentinel_value(self, backend: "EpicsObjective") -> float | None:
        bounds = getattr(backend, "constraint_bounds", []) or []
        if self.target_col < 0 or self.target_col >= len(bounds):
            return None

        lower, upper = bounds[self.target_col]
        if lower is None and upper is None:
            return None

        if lower is not None and upper is not None:
            delta = max(self.delta_min, self.delta_ratio * (float(upper) - float(lower)))
        else:
            bound = float(upper) if upper is not None else float(lower)
            delta = max(self.delta_min, self.delta_ratio * max(abs(bound), self.scale_floor))

        if upper is not None:
            return float(upper) + delta
        return float(lower) - delta

    def post_reduce(
        self,
        results: np.ndarray,
        total: np.ndarray,
        backend: "EpicsObjective",
    ) -> np.ndarray:
        results = np.asarray(results, dtype=float).copy()

        if results.ndim != 1 or total.ndim != 2:
            return results
        if self.target_col < 0 or self.target_col >= len(results):
            return results
        if self.target_col >= total.shape[1]:
            return results

        col = np.asarray(total[:, self.target_col], dtype=float)
        if col.size == 0 or not np.all(np.isfinite(col)):
            return results

        if np.max(np.abs(col)) > self.zero_atol:
            return results

        sentinel = self._sentinel_value(backend)
        if sentinel is None:
            return results

        results[self.target_col] = sentinel
        return results


# =============================================================================
# 通用 EPICS objective
# =============================================================================
class EpicsObjective(ObjectiveBackend):
    """
    通用 EPICS-backed objective。

    参数
    ----
    knobs_pvnames:
        决策变量对应的 PV 列表

    obj_pvnames:
        目标 PV 列表

    obj_weights:
        目标权重
        - weighted_sum 模式下：做 dot(results, weights)
        - vector 模式下：做 results * weights

    obj_samples:
        每次 evaluate 时采样次数

    obj_math:
        每个目标列的聚合方式，支持:
            "mean"
            "std"

    set_interval:
        写入 setpoint 后等待机器稳定的时间

    sample_interval:
        同一次 evaluate 内多次采样 objective/constraint PV 之间的等待时间

    log_path:
        记录优化历史到哪个文件

    readback_check:
        是否在写入后进行 readback 校验

    readback_tol:
        readback 容忍误差，可以是标量或与 knob 数量等长的数组

    combine_mode:
        "weighted_sum" -> 返回标量，适合单目标优化器
        "vector"       -> 返回向量，适合多目标优化器

    write_policy:
        写入策略。若为 None，则使用 DefaultWritePolicy

    objective_policy:
        目标策略。若为 None，则不做额外处理

    constraint_policy:
        约束策略。若为 None，则不做额外处理

    best_selector_mode:
        当 backend 自己从 self.data 中选 best 时采用的策略。
        可选：
            None
            "max"      -> 标量最大
            "min"      -> 标量最小
            "sum_max"  -> 向量行和最大
            "sum_min"  -> 向量行和最小
    """

    def __init__(
        self,
        knobs_pvnames: Sequence[str] | None = None,
        knob_readback_pvnames: Sequence[str] | None = None,
        obj_pvnames: Sequence[str] | None = None,
        objective_names: Sequence[str] | None = None,
        obj_weights: Sequence[float] | None = None,
        obj_samples: int | None = None,
        obj_math: Sequence[str] | None = None,
        constraint_pvnames: Sequence[str] | None = None,
        constraint_names: Sequence[str] | None = None,
        constraint_math: Sequence[str] | None = None,
        constraint_bounds: Sequence[tuple[float | None, float | None]] | None = None,
        set_interval: float | None = None,
        sample_interval: float | None = None,
        log_path: str = "template.opt",
        readback_check: bool = False,
        readback_tol: float | Sequence[float] | None = None,
        combine_mode: str = "weighted_sum",
        write_policy: BaseWritePolicy | None = None,
        objective_policy: BaseObjectivePolicy | None = None,
        constraint_policy: BaseConstraintPolicy | None = None,
        best_selector_mode: str | None = None,
    ) -> None:
        # 延迟导入 EPICS
        self._caget, self._caput, self._caput_many, self._caget_many = _load_epics()

        # 基本配置
        self.knobs_pvnames = list(knobs_pvnames) if knobs_pvnames is not None else []
        self.knob_readback_pvnames = (
            list(knob_readback_pvnames) if knob_readback_pvnames is not None else list(self.knobs_pvnames)
        )
        self.obj_pvnames = list(obj_pvnames) if obj_pvnames is not None else []
        self.objective_names = (
            list(objective_names) if objective_names is not None else list(self.obj_pvnames)
        )
        self.obj_weights = np.asarray(obj_weights if obj_weights is not None else [], dtype=float)
        self.obj_samples = int(obj_samples) if obj_samples is not None else 1
        self.obj_math = list(obj_math) if obj_math is not None else []
        self.constraint_pvnames = list(constraint_pvnames) if constraint_pvnames is not None else []
        self.constraint_names = (
            list(constraint_names)
            if constraint_names is not None
            else list(self.constraint_pvnames)
        )
        self.constraint_math = list(constraint_math) if constraint_math is not None else []
        self.constraint_bounds = list(constraint_bounds) if constraint_bounds is not None else []
        self.set_interval = float(set_interval) if set_interval is not None else 0.0
        self.sample_interval = float(sample_interval) if sample_interval is not None else 0.0

        # 运行辅助配置
        self.log_path = str(log_path)
        self.readback_check = bool(readback_check)
        self.readback_tol = readback_tol
        self.combine_mode = str(combine_mode).lower()
        self.best_selector_mode = best_selector_mode

        # policy
        self.write_policy = write_policy if write_policy is not None else DefaultWritePolicy()
        self.objective_policy = objective_policy
        self.constraint_policy = constraint_policy

        # 数据记录
        # self.data 每一行统一记录成：
        #   [x..., y...]，其中 y 可以是 1 列（标量）或多列（向量）
        self.data: list[np.ndarray] = []
        self.constraint_data: list[np.ndarray] = []

        # 初值，用于恢复
        self.initial_knob_values: np.ndarray | None = None

        self._validate_config()

    # -------------------------------------------------------------------------
    # 配置检查
    # -------------------------------------------------------------------------
    def _validate_config(self) -> None:
        if len(self.knobs_pvnames) == 0:
            raise ValueError("knobs_pvnames cannot be empty")

        if len(self.knob_readback_pvnames) != len(self.knobs_pvnames):
            raise ValueError("knob_readback_pvnames length must match knobs_pvnames")

        if len(self.obj_pvnames) == 0:
            raise ValueError("obj_pvnames cannot be empty")

        if len(self.obj_weights) != len(self.obj_pvnames):
            raise ValueError("obj_weights length must match obj_pvnames")

        if len(self.objective_names) != len(self.obj_pvnames):
            raise ValueError("objective_names length must match obj_pvnames")

        if len(self.obj_math) != len(self.obj_pvnames):
            raise ValueError("obj_math length must match obj_pvnames")

        if len(self.constraint_math) != len(self.constraint_pvnames):
            raise ValueError("constraint_math length must match constraint_pvnames")

        if len(self.constraint_names) != len(self.constraint_pvnames):
            raise ValueError("constraint_names length must match constraint_pvnames")

        if self.constraint_bounds and len(self.constraint_bounds) != len(self.constraint_pvnames):
            raise ValueError("constraint_bounds length must match constraint_pvnames")

        if self.obj_samples < 1:
            raise ValueError("obj_samples must be >= 1")

        if self.set_interval < 0:
            raise ValueError("set_interval must be >= 0")

        if self.sample_interval < 0:
            raise ValueError("sample_interval must be >= 0")

        if self.combine_mode not in {"weighted_sum", "vector"}:
            raise ValueError(
                f"Unsupported combine_mode={self.combine_mode!r}, "
                "must be 'weighted_sum' or 'vector'"
            )

        if self.objective_policy is not None:
            self.objective_policy.validate_backend(self)
        if self.constraint_policy is not None:
            self.constraint_policy.validate_backend(self)

    # -------------------------------------------------------------------------
    # 基础工具函数
    # -------------------------------------------------------------------------
    def _ensure_parent_dir(self, path: str) -> None:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

    def _read_many_checked(self, pvnames: Sequence[str], what: str = "PV") -> np.ndarray:
        """
        统一读取多个 PV，并对返回结果做 None 检查。
        """
        pv_list = list(pvnames)
        values = self._caget_many(pv_list)
        if values is None:
            raise RuntimeError(f"{what} read failed: returned None")

        values = list(values)
        timeout = max(1.0, float(self.set_interval), float(self.sample_interval))
        missing_indices = [i for i, value in enumerate(values) if value is None]
        for idx in missing_indices:
            values[idx] = self._caget(pv_list[idx], timeout=timeout)

        if any(v is None for v in values):
            missing_pvs = [str(pv) for pv, value in zip(pv_list, values) if value is None]
            if missing_pvs:
                raise RuntimeError(
                    f"{what} read failed: contains None for PVs {missing_pvs}"
                )
            raise RuntimeError(f"{what} read failed: contains None")
        return np.asarray(values, dtype=float)

    def _normalize_y_to_1d(self, y: np.ndarray | float) -> np.ndarray:
        """
        把最终目标 y 统一转成一维数组，便于日志记录。
        """
        if np.isscalar(y):
            return np.asarray([float(y)], dtype=float)
        arr = np.asarray(y, dtype=float).reshape(-1)
        return arr

    def _record_evaluate(
        self,
        x: np.ndarray,
        y: np.ndarray | float,
        constraints: np.ndarray | None = None,
    ) -> None:
        """
        记录一次评估结果到内存和文件。

        统一格式：
            row = [x..., y...]
        """
        x = np.asarray(x, dtype=float).reshape(-1)
        y_arr = self._normalize_y_to_1d(y)

        row = np.concatenate([x, y_arr])
        self.data.append(row)
        if constraints is not None:
            self.constraint_data.append(np.asarray(constraints, dtype=float).reshape(-1))

        self._ensure_parent_dir(self.log_path)
        np.savetxt(self.log_path, np.asarray(self.data, dtype=float), fmt="%.6f")

    # -------------------------------------------------------------------------
    # 与历史脚本一致/兼容的方法
    # -------------------------------------------------------------------------
    def init_knob_value(self) -> np.ndarray:
        """
        读取当前 knobs 初始值，并在第一次调用时保存下来，供恢复使用。
        """
        knob_values = self._read_many_checked(self.knob_readback_pvnames, what="initial knob PV")
        if self.initial_knob_values is None:
            self.initial_knob_values = knob_values.copy()
        return knob_values

    def restore_initial(self) -> None:
        """
        恢复到初始化时的 knob 值。

        这里故意调用与正常写入相同的 _apply_setpoints()，
        这样即便是消色散那种“还要额外联动写两个四极”的场景，
        恢复动作也会走同一套写入逻辑。
        """
        if self.initial_knob_values is None:
            raise RuntimeError("Initial knob values not saved yet. Call init_knob_value() first.")

        self._apply_setpoints(self.initial_knob_values)
        print(f"[EpicsObjective] Restored initial knob values: {self.initial_knob_values}")

    def set_init(self) -> None:
        """
        兼容旧脚本命名。
        """
        self.restore_initial()

    def set_initial(self) -> None:
        """
        再提供一个兼容别名，方便 runner 的“保险式”调用。
        """
        self.restore_initial()

    # -------------------------------------------------------------------------
    # 写入 / readback / 目标聚合
    # -------------------------------------------------------------------------
    def _apply_setpoints(self, x: np.ndarray) -> None:
        """
        统一写入入口。
        默认走 DefaultWritePolicy；
        特殊问题可以替换成自定义 write_policy。
        """
        x = np.asarray(x, dtype=float).reshape(-1)
        if len(x) != len(self.knobs_pvnames):
            raise ValueError(
                f"Setpoint dimension mismatch: got {len(x)} value(s), "
                f"expected {len(self.knobs_pvnames)} knob(s)."
            )
        self.write_policy.apply(self, x)

    def _check_readback(self, target_x: np.ndarray) -> None:
        """
        可选 readback 检查：
        在写入 knobs 后，再把 knobs_pvnames 读回来，与 target_x 对比。
        """
        rb = self._read_many_checked(self.knob_readback_pvnames, what="knob readback")
        target_x = np.asarray(target_x, dtype=float).reshape(-1)

        if self.readback_tol is None:
            tol = np.zeros_like(target_x)
        else:
            tol = np.asarray(self.readback_tol, dtype=float)
            if tol.ndim == 0:
                tol = np.full_like(target_x, float(tol))
            elif tol.shape != target_x.shape:
                raise ValueError(
                    "readback_tol must be a scalar or an array with the same length as knobs"
                )

        diff = np.abs(rb - target_x)
        ok = np.all(diff <= tol)

        if not ok:
            raise RuntimeError(
                "Readback check failed.\n"
                f"target   = {target_x}\n"
                f"readback = {rb}\n"
                f"abs diff = {diff}\n"
                f"tol      = {tol}"
            )

    def _reduce_columns(self, total: np.ndarray, math_ops: Sequence[str], *, what: str) -> np.ndarray:
        """
        将采样矩阵 total 按 math_ops 做列聚合。

        total.shape = (obj_samples, n_outputs)
        """
        results = []
        for col, op in zip(total.T, math_ops):
            if op == "mean":
                results.append(np.mean(col))
            elif op == "std":
                results.append(np.std(col))
            else:
                raise ValueError(f"Unsupported {what}_math operation: {op!r}")

        return np.asarray(results, dtype=float)

    def _reduce_objectives(self, total: np.ndarray) -> np.ndarray:
        return self._reduce_columns(total, self.obj_math, what="obj")

    def _reduce_constraints(self, total: np.ndarray) -> np.ndarray:
        if len(self.constraint_pvnames) == 0:
            return np.zeros((0,), dtype=float)
        return self._reduce_columns(total, self.constraint_math, what="constraint")

    def _sample_outputs(self) -> tuple[np.ndarray, np.ndarray]:
        """
        同一轮采样内同时读取 objective 与 constraint PV。

        对每个 sample step，合并读取 obj_pvnames + constraint_pvnames，
        再拆分为两个采样矩阵，保证约束和目标尽可能对应同一时刻。
        """
        n_obj = len(self.obj_pvnames)
        n_cons = len(self.constraint_pvnames)
        obj_total = np.zeros((self.obj_samples, n_obj), dtype=float)
        constraint_total = np.zeros((self.obj_samples, n_cons), dtype=float)
        all_pvs = [*self.obj_pvnames, *self.constraint_pvnames]

        for i in range(self.obj_samples):
            vals = self._read_many_checked(all_pvs, what="objective/constraint PV")
            obj_total[i, :] = vals[:n_obj]
            if n_cons:
                constraint_total[i, :] = vals[n_obj:]
            if i < self.obj_samples - 1 and self.sample_interval > 0:
                time.sleep(self.sample_interval)

        return obj_total, constraint_total

    def _combine_objectives(self, results: np.ndarray) -> np.ndarray | float:
        """
        把聚合后的 results 组合成优化器真正看到的 y。

        weighted_sum:
            返回标量，适合单目标优化器

        vector:
            返回逐元素乘权后的向量，适合多目标优化器
        """
        results = np.asarray(results, dtype=float).reshape(-1)

        if self.combine_mode == "weighted_sum":
            return float(np.dot(results, self.obj_weights))

        if self.combine_mode == "vector":
            return results * self.obj_weights

        raise ValueError(f"Unsupported combine_mode: {self.combine_mode!r}")

    # -------------------------------------------------------------------------
    # 核心评估函数
    # -------------------------------------------------------------------------
    def _evaluate_core(
        self,
        x: np.ndarray | Sequence[float],
        *,
        return_constraints: bool = False,
    ) -> np.ndarray | float | tuple[np.ndarray | float, np.ndarray]:
        x = np.asarray(x, dtype=float).reshape(-1)

        if len(x) != len(self.knobs_pvnames):
            raise ValueError(
                f"Input dimension mismatch: len(x)={len(x)}, "
                f"len(knobs_pvnames)={len(self.knobs_pvnames)}"
            )

        print(f"[EpicsObjective] Evaluating with x = {x}")

        # 1) 写入
        self._apply_setpoints(x)

        # 2) 等待机器稳定
        if self.set_interval > 0:
            time.sleep(self.set_interval)

        # 3) 可选 readback
        if self.readback_check:
            self._check_readback(x)

        # 4) 多次采样目标与约束。每一轮采样合并读 PV，避免目标/约束错时。
        total, constraint_total = self._sample_outputs()

        # 5) 允许 policy 在原始采样层面做预处理
        if self.objective_policy is not None:
            total = self.objective_policy.preprocess_total(total, self)
        if self.constraint_policy is not None:
            constraint_total = self.constraint_policy.preprocess_total(constraint_total, self)

        # 6) 列聚合
        results = self._reduce_objectives(total)

        # 7) 允许 policy 在聚合结果层面做修正
        if self.objective_policy is not None:
            results = self.objective_policy.post_reduce(results, total, self)

        # 8) 组合成最终 y
        y = self._combine_objectives(results)
        constraints = self._reduce_constraints(constraint_total)
        if self.constraint_policy is not None:
            constraints = self.constraint_policy.post_reduce(constraints, constraint_total, self)

        # 9) 允许 policy 在最终 y 层面做修正
        if self.objective_policy is not None:
            y = self.objective_policy.post_combine(y, results, total, self)

        print(f"[EpicsObjective] Reduced results = {results}")
        if len(self.constraint_pvnames):
            print(f"[EpicsObjective] Reduced constraints = {constraints}")
        print(f"[EpicsObjective] Final objective = {y}")

        # 10) 记录
        self._record_evaluate(x, y, constraints if return_constraints else None)

        if return_constraints:
            return y, constraints
        return y

    def evaluate(self, x: np.ndarray | Sequence[float]) -> np.ndarray | float:
        """
        普通优化器使用的评估入口，只返回 objective。
        """
        return self._evaluate_core(x, return_constraints=False)

    def evaluate_with_constraints(self, x: np.ndarray | Sequence[float]) -> tuple[np.ndarray | float, np.ndarray]:
        """
        Constrained BO 使用的评估入口，返回 (objective, constraints)。
        """
        if not self.constraint_pvnames:
            raise RuntimeError("No constraint_pvnames configured for constrained evaluation.")
        y, constraints = self._evaluate_core(x, return_constraints=True)
        return y, np.asarray(constraints, dtype=float).reshape(-1)

    def evaluate_func(self, x: np.ndarray | Sequence[float]) -> np.ndarray | float:
        """
        兼容旧优化器/旧 runner 对 evaluate_func() 的调用习惯。
        """
        return self.evaluate(x)

    # -------------------------------------------------------------------------
    # 选 best / 写 best
    # -------------------------------------------------------------------------
    def _split_logged_data(self) -> tuple[np.ndarray, np.ndarray]:
        """
        从 self.data 中拆出：
            X_history, Y_history

        X_history.shape = (n_eval, n_knobs)
        Y_history.shape = (n_eval, n_y)

        其中：
            n_y = 1  表示标量目标
            n_y > 1  表示向量目标
        """
        if len(self.data) == 0:
            raise RuntimeError("No evaluation data recorded yet.")

        data_array = np.asarray(self.data, dtype=float)
        n_knobs = len(self.knobs_pvnames)

        if data_array.shape[1] <= n_knobs:
            raise RuntimeError("Logged data shape is invalid: no objective columns found.")

        x_hist = data_array[:, :n_knobs]
        y_hist = data_array[:, n_knobs:]
        return x_hist, y_hist

    def get_best(self) -> tuple[np.ndarray, np.ndarray | float]:
        """
        从 self.data 中找“当前 backend 视角下的 best”。

        注意：
        ----
        对标量目标，这个 best 很自然；
        对多目标向量，没有 universally correct 的 best 定义，
        所以这里采用以下优先顺序：


        若 best_selector_mode 明确指定，则按它来
        再否则：
           - weighted_sum -> 取最大值（单目标默认优化最大值）
           - vector       -> 取行和最小值（多目标默认优化最小值）
        """
        x_hist, y_hist = self._split_logged_data()

        # 先问 objective_policy 要不要自定义 best 规则
        if self.objective_policy is not None:
            idx = self.objective_policy.select_best_index(y_hist, self)
            if idx is not None:
                best_x = x_hist[idx]
                best_y = y_hist[idx]
                if best_y.size == 1:
                    return best_x, float(best_y[0])
                return best_x, best_y

        mode = self.best_selector_mode

        # 默认策略
        if mode is None:
            if y_hist.shape[1] == 1:
                mode = "max"
            else:
                # 与你旧消色散脚本尽量接近：对向量目标先求和，再取最小值
                mode = "sum_min"

        # 下面开始真正选 best
        if y_hist.shape[1] == 1:
            scores = y_hist[:, 0]

            if mode == "max":
                best_idx = int(np.argmax(scores))
            elif mode == "min":
                best_idx = int(np.argmin(scores))
            else:
                raise ValueError(
                    f"Scalar objective only supports best_selector_mode='max' or 'min', got {mode!r}"
                )

            best_x = x_hist[best_idx]
            best_y = float(scores[best_idx])
            return best_x, best_y

        # 向量目标
        row_sums = np.sum(y_hist, axis=1)

        if mode == "sum_max":
            best_idx = int(np.argmax(row_sums))
        elif mode == "sum_min":
            best_idx = int(np.argmin(row_sums))
        else:
            raise ValueError(
                f"Vector objective only supports best_selector_mode='sum_max' or 'sum_min', got {mode!r}"
            )

        best_x = x_hist[best_idx]
        best_y = y_hist[best_idx]
        return best_x, best_y

    def set_best(self, optimizer: Any | None = None) -> None:
        """
        将 backend 认为的 best_x 写回 EPICS。

        这里暂时仍采用“从 self.data 中选 best”的方式，
        而不是依赖不同优化器内部五花八门的 best_x/best_y 属性，
        这样对现阶段更稳。

        以后如果你确定某些优化器都有统一 best 接口，
        再改成优先读 optimizer 的结果也不迟。
        """
        best_x, best_y = self.get_best()
        self._apply_setpoints(np.asarray(best_x, dtype=float))

        print(f"[EpicsObjective] Best x has been written back to EPICS: {best_x}")
        print(f"[EpicsObjective] Corresponding best y: {best_y}")

    def close(self) -> None:
        """
        预留资源释放接口。
        当前 pyepics 的这种用法不一定需要显式 close，但保留接口更稳妥。
        """
        return None
