"""Composable orchestration stages for adaptive energy tuning."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .pipeline import BRIGHTNESS_PEAK, CENTER_LOCK, normalize_pipeline


@dataclass(frozen=True)
class StageResult:
    """Result produced by one tuning stage."""

    ok: bool
    actuator_value: float | None
    message: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class EnergyStageContext:
    """Application-provided hooks used by shared stage orchestration."""

    low: float
    high: float
    initial_value: float
    config: Mapping[str, Any]
    brightness_peak: Callable[[float, float, Mapping[str, Any]], StageResult]
    center_lock: Callable[[float, float, float, Mapping[str, Any]], StageResult]


class BrightnessPeakStage:
    name = BRIGHTNESS_PEAK

    def run(self, context: EnergyStageContext, current_value: float) -> StageResult:
        config = context.config.get(self.name, context.config)
        return context.brightness_peak(context.low, context.high, config)


class CenterLockStage:
    name = CENTER_LOCK

    def run(self, context: EnergyStageContext, current_value: float) -> StageResult:
        config = context.config.get(self.name, context.config)
        return context.center_lock(
            current_value,
            context.low,
            context.high,
            config,
        )


class EnergyTuningPipeline:
    """Run configured stages in order and pass each result to the next stage."""

    def __init__(self, pipeline=None, *, legacy_objective=None):
        self.pipeline = normalize_pipeline(pipeline, legacy_objective=legacy_objective)
        self._stages = {
            BRIGHTNESS_PEAK: BrightnessPeakStage(),
            CENTER_LOCK: CenterLockStage(),
        }

    def run(self, context: EnergyStageContext) -> StageResult:
        value = float(context.initial_value)
        diagnostics: dict[str, Any] = {"pipeline": list(self.pipeline), "stages": []}
        for stage_name in self.pipeline:
            result = self._stages[stage_name].run(context, value)
            diagnostics["stages"].append(
                {
                    "name": stage_name,
                    "ok": bool(result.ok),
                    "actuator_value": result.actuator_value,
                    **dict(result.diagnostics),
                }
            )
            if not result.ok or result.actuator_value is None:
                return StageResult(
                    ok=False,
                    actuator_value=result.actuator_value,
                    message=result.message,
                    diagnostics=diagnostics,
                )
            value = float(result.actuator_value)
        return StageResult(True, value, diagnostics=diagnostics)
