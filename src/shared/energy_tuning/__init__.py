"""Shared stage configuration for adaptive energy tuning workflows."""

from .pipeline import (
    BRIGHTNESS_PEAK,
    CENTER_LOCK,
    LEGACY_OBJECTIVE_PIPELINES,
    EnergyTuningPipelineError,
    normalize_pipeline,
    legacy_objective_for_pipeline,
    pipeline_has,
)
from .actuator import CallableEnergyActuator, EnergyActuator
from .measurement import BeamMeasurement, ScreenProfileMeasurement
from .models import EnergyObservation, EnergyTuneResult
from .stages import (
    BrightnessPeakStage,
    CenterLockStage,
    EnergyStageContext,
    EnergyTuningPipeline,
    StageResult,
)

__all__ = [
    "BRIGHTNESS_PEAK",
    "CENTER_LOCK",
    "LEGACY_OBJECTIVE_PIPELINES",
    "EnergyTuningPipelineError",
    "normalize_pipeline",
    "legacy_objective_for_pipeline",
    "pipeline_has",
    "CallableEnergyActuator",
    "EnergyActuator",
    "ScreenProfileMeasurement",
    "BeamMeasurement",
    "EnergyObservation",
    "EnergyTuneResult",
    "BrightnessPeakStage",
    "CenterLockStage",
    "EnergyStageContext",
    "EnergyTuningPipeline",
    "StageResult",
]
