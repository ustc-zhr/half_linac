from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from gotacc.interfaces.epics import BaseConstraintPolicy, BaseObjectivePolicy


_METRICS = {"mean_abs", "max_abs", "peak_to_peak", "mean", "std", "reduced"}
_OPERATORS = {
    "gt": operator.gt,
    ">": operator.gt,
    "ge": operator.ge,
    ">=": operator.ge,
    "lt": operator.lt,
    "<": operator.lt,
    "le": operator.le,
    "<=": operator.le,
}
_EQUALITY_OPERATORS = {"eq", "==", "ne", "!="}


@dataclass(frozen=True)
class _Condition:
    metric: str
    operator_name: str
    value: float
    atol: float = 0.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], index: int) -> "_Condition":
        if not isinstance(raw, Mapping):
            raise TypeError(f"conditions[{index}] must be a mapping")
        metric = str(raw.get("metric", "")).strip().lower()
        operator_name = str(raw.get("operator", "")).strip().lower()
        if metric not in _METRICS:
            raise ValueError(
                f"conditions[{index}].metric={metric!r} is unsupported; "
                f"use one of {sorted(_METRICS)}"
            )
        if operator_name not in {*_OPERATORS, *_EQUALITY_OPERATORS}:
            raise ValueError(
                f"conditions[{index}].operator={operator_name!r} is unsupported"
            )
        try:
            value = float(raw["value"])
            atol = float(raw.get("atol", 0.0))
        except KeyError as exc:
            raise ValueError(f"conditions[{index}] must define 'value'") from exc
        except (TypeError, ValueError) as exc:
            raise TypeError(f"conditions[{index}] value and atol must be numeric") from exc
        if atol < 0:
            raise ValueError(f"conditions[{index}].atol must be >= 0")
        if not np.isfinite(value) or not np.isfinite(atol):
            raise ValueError(f"conditions[{index}] value and atol must be finite")
        return cls(metric=metric, operator_name=operator_name, value=value, atol=atol)

    def matches(self, actual: float) -> bool:
        if self.operator_name in _EQUALITY_OPERATORS:
            equal = bool(np.isclose(actual, self.value, atol=self.atol, rtol=0.0))
            return equal if self.operator_name in {"eq", "=="} else not equal
        return bool(_OPERATORS[self.operator_name](actual, self.value))


class _SampleGuardRule:
    def __init__(
        self,
        *,
        kind: str,
        target: str | int | None,
        target_col: int,
        conditions: Sequence[Mapping[str, Any]],
        match: str,
        action: Mapping[str, Any],
    ) -> None:
        self.kind = str(kind).strip().lower()
        if self.kind not in {"objective", "constraint"}:
            raise ValueError(f"Unsupported sample guard kind: {kind!r}")
        self.target = target
        self.target_col = int(target_col)
        if self.target_col < 0:
            raise ValueError("target_col must be >= 0")
        if not isinstance(conditions, Sequence) or isinstance(conditions, (str, bytes)):
            raise TypeError("conditions must be a sequence of mappings")
        if len(conditions) == 0:
            raise ValueError("conditions must contain at least one condition")
        self.conditions = tuple(
            _Condition.from_mapping(condition, index)
            for index, condition in enumerate(conditions)
        )
        self.match = str(match).strip().lower()
        if self.match not in {"any", "all"}:
            raise ValueError("match must be 'any' or 'all'")
        if not isinstance(action, Mapping):
            raise TypeError("action must be a mapping")
        self.action = dict(action)
        self.action_type = str(self.action.get("type", "")).strip().lower()
        allowed_actions = (
            {"replace", "add_offset"}
            if self.kind == "objective"
            else {"replace", "violate_bound"}
        )
        if self.action_type not in allowed_actions:
            raise ValueError(
                f"Unsupported {self.kind} sample guard action {self.action_type!r}; "
                f"use one of {sorted(allowed_actions)}"
            )
        if self.action_type in {"replace", "add_offset"}:
            try:
                value = float(self.action["value"])
            except KeyError as exc:
                raise ValueError(f"action type {self.action_type!r} requires 'value'") from exc
            except (TypeError, ValueError) as exc:
                raise TypeError("action.value must be numeric") from exc
            if not np.isfinite(value):
                raise ValueError("action.value must be finite")
            self.action["value"] = value
        if self.action_type == "violate_bound":
            for key, default in (
                ("delta_ratio", 0.1),
                ("delta_min", 1e-6),
                ("scale_floor", 1.0),
            ):
                try:
                    value = float(self.action.get(key, default))
                except (TypeError, ValueError) as exc:
                    raise TypeError(f"action.{key} must be numeric") from exc
                if not np.isfinite(value) or value < 0:
                    raise ValueError(f"action.{key} must be finite and >= 0")
                self.action[key] = value

    def _candidate_names(self, backend: Any) -> tuple[list[str], list[str]]:
        if self.kind == "objective":
            return (
                list(getattr(backend, "objective_names", []) or []),
                list(getattr(backend, "obj_pvnames", []) or []),
            )
        return (
            list(getattr(backend, "constraint_names", []) or []),
            list(getattr(backend, "constraint_pvnames", []) or []),
        )

    def resolve_target_col(self, backend: Any) -> int:
        if self.target is None or str(self.target).strip() == "":
            index = self.target_col
        elif isinstance(self.target, (int, np.integer)):
            index = int(self.target)
        else:
            target = str(self.target).strip()
            name_sets = self._candidate_names(backend)
            exact_matches = [
                index
                for names in name_sets
                for index, name in enumerate(names)
                if str(name).strip() == target
            ]
            if not exact_matches:
                folded_target = target.casefold()
                exact_matches = [
                    index
                    for names in name_sets
                    for index, name in enumerate(names)
                    if str(name).strip().casefold() == folded_target
                ]
            unique_matches = sorted(set(exact_matches))
            if len(unique_matches) != 1:
                available = sorted(
                    {
                        str(name).strip()
                        for names in name_sets
                        for name in names
                        if str(name).strip()
                    }
                )
                raise ValueError(
                    f"Cannot resolve {self.kind} sample guard target {target!r}; "
                    f"available targets: {available}"
                )
            index = unique_matches[0]

        names, pvnames = self._candidate_names(backend)
        target_count = max(len(names), len(pvnames))
        if index < 0 or index >= target_count:
            raise ValueError(
                f"{self.kind} sample guard target index {index} is out of range "
                f"for {target_count} configured target(s)"
            )
        return index

    def target_label(self, backend: Any, index: int) -> str:
        if self.target is not None and str(self.target).strip():
            return str(self.target).strip()
        names, pvnames = self._candidate_names(backend)
        candidates = names or pvnames
        return str(candidates[index]).strip() if index < len(candidates) else f"column {index}"

    def validate_backend(self, backend: Any) -> None:
        self.resolve_target_col(backend)

    @staticmethod
    def _metric_value(
        metric: str,
        samples: np.ndarray,
        reduced_value: float,
    ) -> float:
        if metric == "mean_abs":
            return float(np.mean(np.abs(samples)))
        if metric == "max_abs":
            return float(np.max(np.abs(samples)))
        if metric == "peak_to_peak":
            return float(np.max(samples) - np.min(samples))
        if metric == "mean":
            return float(np.mean(samples))
        if metric == "std":
            return float(np.std(samples))
        if metric == "reduced":
            return float(reduced_value)
        raise AssertionError(f"Unexpected metric after validation: {metric!r}")

    def triggered(self, results: np.ndarray, total: np.ndarray, backend: Any) -> tuple[bool, int]:
        index = self.resolve_target_col(backend)
        if results.ndim != 1 or total.ndim != 2 or index >= total.shape[1]:
            return False, index
        samples = np.asarray(total[:, index], dtype=float)
        if samples.size == 0:
            return False, index
        matches = [
            condition.matches(
                self._metric_value(condition.metric, samples, float(results[index]))
            )
            for condition in self.conditions
        ]
        return (any(matches) if self.match == "any" else all(matches)), index

    def constraint_violation_value(self, backend: Any, index: int) -> float:
        bounds = list(getattr(backend, "constraint_bounds", []) or [])
        if index >= len(bounds):
            raise ValueError(
                "constraint sample guard action 'violate_bound' requires a configured bound"
            )
        lower, upper = bounds[index]
        if lower is None and upper is None:
            raise ValueError(
                "constraint sample guard action 'violate_bound' requires a lower or upper bound"
            )
        delta_ratio = float(self.action["delta_ratio"])
        delta_min = float(self.action["delta_min"])
        scale_floor = float(self.action["scale_floor"])
        if lower is not None and upper is not None:
            delta = max(delta_min, delta_ratio * (float(upper) - float(lower)))
        else:
            bound = float(upper) if upper is not None else float(lower)
            delta = max(delta_min, delta_ratio * max(abs(bound), scale_floor))
        return float(upper) + delta if upper is not None else float(lower) - delta


class SampleGuardObjectivePolicy(BaseObjectivePolicy):
    def __init__(
        self,
        *,
        target: str | int | None = None,
        target_col: int = 0,
        conditions: Sequence[Mapping[str, Any]],
        match: str = "any",
        action: Mapping[str, Any],
    ) -> None:
        self.rule = _SampleGuardRule(
            kind="objective",
            target=target,
            target_col=target_col,
            conditions=conditions,
            match=match,
            action=action,
        )

    def validate_backend(self, backend: Any) -> None:
        self.rule.validate_backend(backend)

    def post_reduce(self, results: np.ndarray, total: np.ndarray, backend: Any) -> np.ndarray:
        results = np.asarray(results, dtype=float).copy()
        triggered, index = self.rule.triggered(results, total, backend)
        if not triggered:
            return results
        before = float(results[index])
        value = float(self.rule.action["value"])
        if self.rule.action_type == "replace":
            results[index] = value
        else:
            results[index] += value
        target = self.rule.target_label(backend, index)
        action = self.rule.action_type.replace("_", " ")
        self.emit_event(
            f"Policy triggered for {target} [{action}]: "
            f"{before:.6g} → {float(results[index]):.6g}"
        )
        return results


class SampleGuardConstraintPolicy(BaseConstraintPolicy):
    def __init__(
        self,
        *,
        target: str | int | None = None,
        target_col: int = 0,
        conditions: Sequence[Mapping[str, Any]],
        match: str = "any",
        action: Mapping[str, Any],
    ) -> None:
        self.rule = _SampleGuardRule(
            kind="constraint",
            target=target,
            target_col=target_col,
            conditions=conditions,
            match=match,
            action=action,
        )

    def validate_backend(self, backend: Any) -> None:
        self.rule.validate_backend(backend)
        if self.rule.action_type == "violate_bound":
            index = self.rule.resolve_target_col(backend)
            self.rule.constraint_violation_value(backend, index)

    def post_reduce(self, results: np.ndarray, total: np.ndarray, backend: Any) -> np.ndarray:
        results = np.asarray(results, dtype=float).copy()
        triggered, index = self.rule.triggered(results, total, backend)
        if not triggered:
            return results
        before = float(results[index])
        if self.rule.action_type == "replace":
            results[index] = float(self.rule.action["value"])
        else:
            results[index] = self.rule.constraint_violation_value(backend, index)
        target = self.rule.target_label(backend, index)
        action = self.rule.action_type.replace("_", " ")
        self.emit_event(
            f"Policy triggered for {target} [{action}]: "
            f"{before:.6g} → {float(results[index]):.6g}"
        )
        return results
