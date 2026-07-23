from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from half_linac.src.apps.dispersion_correction.knobs import SymmetricKnobSet
from half_linac.src.apps.dispersion_correction.models import (
    CorrectionRecommendation,
    DispersionMeasurement,
    ResponseMatrixResult,
    RunConfig,
)
from half_linac.src.apps.dispersion_correction.solver import solve_bounded_correction


def build_correction_recommendation(
    config: RunConfig,
    measurement: DispersionMeasurement,
    response: ResponseMatrixResult,
    *,
    baseline_device_values: Mapping[str, float] | None = None,
) -> CorrectionRecommendation:
    """Calculate one bounded correction step without reading or writing a backend."""

    knob_names = tuple(knob.name for knob in config.knobs)
    if measurement.bpm_names != config.target_bpms:
        raise ValueError("Measurement BPMs do not match the current configuration")
    if response.bpm_names != config.target_bpms:
        raise ValueError("Response BPMs do not match the current configuration")
    if response.knob_names != knob_names:
        raise ValueError("Response knobs do not match the current configuration")
    if response.matrix.shape != (len(config.target_bpms), len(config.knobs)):
        raise ValueError("Response matrix dimensions do not match the current configuration")

    valid = (
        measurement.valid
        & np.isfinite(measurement.residual_values_mm)
        & np.all(np.isfinite(response.matrix), axis=1)
    )
    if not np.any(valid):
        raise ValueError("No valid BPM rows are available for recommendation")

    zero_knobs = {name: 0.0 for name in knob_names}
    knob_set = SymmetricKnobSet(config.knobs, zero_knobs)
    delta, singular_values, condition = solve_bounded_correction(
        response.matrix[valid, :],
        measurement.residual_values_mm[valid],
        config.solver.svd_cut,
        config.solver.gain,
        knob_set.limits(),
        config.solver.max_step_fraction,
        knob_set.vector_from_mapping(zero_knobs),
        knob_set.vector_from_mapping(zero_knobs),
        config.solver.regularization,
    )
    delta_knobs = knob_set.mapping_from_vector(delta)
    device_deltas = knob_set.device_deltas(delta_knobs)

    baseline_source = baseline_device_values or {}
    baseline = {
        device: float(baseline_source[device])
        for device in device_deltas
        if device in baseline_source
    }
    targets = {
        device: baseline[device] + change
        for device, change in device_deltas.items()
        if device in baseline
    }

    predicted_residual = (
        measurement.residual_values_mm + response.matrix @ delta
    )
    predicted_values = measurement.target_values_mm + predicted_residual
    predicted_rms = float(
        np.sqrt(np.mean(np.square(predicted_residual[valid])))
    )
    reason = (
        "Ready for review"
        if np.any(np.abs(delta) > 0)
        else "Solver returned a zero correction"
    )
    return CorrectionRecommendation(
        measurement=measurement,
        response=response,
        delta_knobs=delta_knobs,
        device_deltas=device_deltas,
        baseline_device_values=baseline,
        target_device_values=targets,
        predicted_values_mm=predicted_values,
        predicted_residual_values_mm=predicted_residual,
        valid=valid,
        predicted_rms_mm=predicted_rms,
        singular_values=singular_values,
        condition_number=condition,
        reason=reason,
    )
