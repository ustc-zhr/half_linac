"""Small, device-independent contract for composable energy-tuning stages."""

BRIGHTNESS_PEAK = "brightness_peak"
CENTER_LOCK = "center_lock"

LEGACY_OBJECTIVE_PIPELINES = {
    "find_beam": (BRIGHTNESS_PEAK,),
    "brightness_gated_x_fit": (BRIGHTNESS_PEAK,),
    "center_x_reference": (CENTER_LOCK,),
    "profile_lock": (CENTER_LOCK,),
    "brightness_then_profile_lock": (BRIGHTNESS_PEAK, CENTER_LOCK),
}


class EnergyTuningPipelineError(ValueError):
    """Raised when a configured adaptive-energy pipeline is invalid."""


def normalize_pipeline(pipeline=None, *, legacy_objective=None):
    """Return a validated tuple of stage names.

    ``legacy_objective`` keeps existing machine profiles and saved settings
    working while new callers can select stages explicitly.
    """
    if pipeline is None:
        objective = "profile_lock" if legacy_objective is None else str(legacy_objective).strip()
        try:
            return LEGACY_OBJECTIVE_PIPELINES[objective]
        except KeyError as exc:
            raise EnergyTuningPipelineError(
                f"Unsupported legacy energy-tuning objective: {objective!r}."
            ) from exc

    if isinstance(pipeline, str):
        pipeline = [pipeline]
    if not isinstance(pipeline, (list, tuple)):
        raise EnergyTuningPipelineError("Energy-tuning pipeline must be a list of stages.")

    stages = tuple(str(stage).strip() for stage in pipeline)
    if not stages:
        raise EnergyTuningPipelineError("Energy-tuning pipeline must contain at least one stage.")
    unsupported = tuple(stage for stage in stages if stage not in {BRIGHTNESS_PEAK, CENTER_LOCK})
    if unsupported:
        raise EnergyTuningPipelineError(
            "Unsupported energy-tuning stage(s): " + ", ".join(repr(stage) for stage in unsupported)
        )
    if len(set(stages)) != len(stages):
        raise EnergyTuningPipelineError("Energy-tuning pipeline must not repeat a stage.")
    if CENTER_LOCK in stages and stages[-1] != CENTER_LOCK:
        raise EnergyTuningPipelineError("center_lock must be the final energy-tuning stage.")
    return stages


def pipeline_has(pipeline, stage):
    """Return whether a normalized pipeline contains ``stage``."""
    return str(stage) in pipeline


def legacy_objective_for_pipeline(pipeline):
    """Return the closest legacy objective name for UI and log compatibility."""
    normalized = normalize_pipeline(pipeline)
    for objective, stages in LEGACY_OBJECTIVE_PIPELINES.items():
        if stages == normalized:
            if objective == "profile_lock":
                continue
            return objective
    raise EnergyTuningPipelineError(
        f"No legacy objective maps to pipeline {normalized!r}."
    )
