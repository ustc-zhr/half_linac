from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from half_linac.src.apps.dispersion_correction.models import (
    KnobConfig,
    ModelResponseResult,
    RunConfig,
)
from half_linac.src.apps.dispersion_correction.solver import condition_number
from half_linac.src.shared.machine_profile import AppContext, build_model_backend


ProgressCallback = Callable[[str, int, int], None]


def calculate_model_response(
    context: AppContext,
    config: RunConfig,
    *,
    progress_callback: ProgressCallback | None = None,
) -> ModelResponseResult:
    """Calculate a design-lattice dispersion response without touching VM state."""

    entrance = config.section.model_entrance
    if not entrance:
        raise ValueError("Model response requires section.model_entrance")
    if context.model_backend is None:
        raise ValueError("The selected machine profile has no model backend")

    backend = build_model_backend(context)
    base_k1 = _base_k1_values(backend, config)
    total = 1 + 2 * len(config.knobs)
    _progress(progress_callback, "Calculating design dispersion", 0, total)
    baseline = _dispersion_vector_mm(backend, entrance, config.target_bpms)
    matrix = np.zeros((len(config.target_bpms), len(config.knobs)), dtype=float)

    completed = 1
    for column, knob in enumerate(config.knobs):
        _progress(progress_callback, f"{knob.name} +scan", completed, total)
        plus = _dispersion_vector_mm(
            backend,
            entrance,
            config.target_bpms,
            lattice_overrides=_knob_overrides(base_k1, knob, knob.scan_step),
        )
        completed += 1
        _progress(progress_callback, f"{knob.name} -scan", completed, total)
        minus = _dispersion_vector_mm(
            backend,
            entrance,
            config.target_bpms,
            lattice_overrides=_knob_overrides(base_k1, knob, -knob.scan_step),
        )
        completed += 1
        matrix[:, column] = (plus - minus) / (2.0 * knob.scan_step)

    _, singular_values, vh = np.linalg.svd(matrix, full_matrices=False)
    largest = float(np.max(singular_values)) if singular_values.size else 0.0
    retained = (
        singular_values / largest > config.solver.svd_cut
        if largest > 0
        else np.zeros_like(singular_values, dtype=bool)
    )
    derived_knobs = _derived_knobs(config, vh, retained)
    _progress(progress_callback, "Model response complete", total, total)
    return ModelResponseResult(
        section_id=config.section.id,
        bpm_names=config.target_bpms,
        knob_names=tuple(knob.name for knob in config.knobs),
        baseline_dispersion_mm=baseline,
        target_dispersion_mm=np.asarray(config.section.target_dispersion_mm, dtype=float),
        response_matrix=matrix,
        singular_values=singular_values,
        condition_number=condition_number(singular_values),
        retained_rank=int(np.count_nonzero(retained)),
        derived_knobs=derived_knobs,
    )


def model_response_to_dict(result: ModelResponseResult) -> dict[str, Any]:
    return {
        "section_id": result.section_id,
        "bpm_names": list(result.bpm_names),
        "knob_names": list(result.knob_names),
        "baseline_dispersion_mm": result.baseline_dispersion_mm.tolist(),
        "target_dispersion_mm": result.target_dispersion_mm.tolist(),
        "residual_dispersion_mm": result.residual_dispersion_mm.tolist(),
        "response_matrix": result.response_matrix.tolist(),
        "singular_values": result.singular_values.tolist(),
        "condition_number": result.condition_number,
        "retained_rank": result.retained_rank,
        "derived_knobs": [
            {
                "name": knob.name,
                "devices": dict(knob.devices),
                "scan_step": knob.scan_step,
                "limit": knob.limit,
            }
            for knob in result.derived_knobs
        ],
    }


def format_model_response(result: ModelResponseResult) -> str:
    lines = [
        f"Model dispersion response: {result.section_id}",
        "",
        "BPM design / target / residual (mm):",
    ]
    for index, name in enumerate(result.bpm_names):
        lines.append(
            f"  {name}: {result.baseline_dispersion_mm[index]:.8g} / "
            f"{result.target_dispersion_mm[index]:.8g} / "
            f"{result.residual_dispersion_mm[index]:.8g}"
        )
    lines.extend(
        [
            "",
            "Raw knob response matrix (mm per knob unit):",
            "  " + "  ".join(result.knob_names),
        ]
    )
    for index, name in enumerate(result.bpm_names):
        values = "  ".join(f"{value:.8g}" for value in result.response_matrix[index])
        lines.append(f"  {name}: {values}")
    lines.extend(
        [
            "",
            "Singular values: " + ", ".join(f"{value:.8g}" for value in result.singular_values),
            f"Condition number: {result.condition_number:.8g}",
            f"Retained rank: {result.retained_rank}",
            "",
            "Model-derived orthogonal knobs:",
        ]
    )
    for knob in result.derived_knobs:
        devices = ", ".join(f"{name}*{weight:.8g}" for name, weight in knob.devices.items())
        lines.append(f"  {knob.name}: {devices}")
    return "\n".join(lines) + "\n"


def _base_k1_values(backend, config: RunConfig) -> dict[str, float]:
    values: dict[str, float] = {}
    for knob in config.knobs:
        for device in knob.devices:
            if device in values:
                continue
            element = backend.get_lattice_element(device)
            try:
                values[device] = float(element["K1"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Model quadrupole {device} has no numeric K1") from exc
    return values


def _knob_overrides(
    base_k1: Mapping[str, float],
    knob: KnobConfig,
    step: float,
) -> dict[str, dict[str, float]]:
    return {
        device: {"K1": base_k1[device] + float(weight) * float(step)}
        for device, weight in knob.devices.items()
    }


def _dispersion_vector_mm(
    backend,
    entrance: str,
    bpm_names: tuple[str, ...],
    *,
    lattice_overrides: Mapping[str, Mapping[str, float]] | None = None,
) -> np.ndarray:
    return np.asarray(
        [
            1000.0
            * backend.get_matrix_element(
                entrance,
                bpm,
                0,
                5,
                lattice_overrides=lattice_overrides,
            )
            for bpm in bpm_names
        ],
        dtype=float,
    )


def _derived_knobs(
    config: RunConfig,
    vh: np.ndarray,
    retained: np.ndarray,
) -> tuple[KnobConfig, ...]:
    derived = []
    for mode_index in np.flatnonzero(retained):
        coefficients = np.asarray(vh[mode_index], dtype=float)
        scale = float(np.max(np.abs(coefficients)))
        if scale <= 0:
            continue
        coefficients = coefficients / scale
        first_nonzero = next((value for value in coefficients if abs(value) > 1.0e-12), 1.0)
        if first_nonzero < 0:
            coefficients = -coefficients
        devices: dict[str, float] = {}
        for coefficient, raw_knob in zip(coefficients, config.knobs):
            for device, weight in raw_knob.devices.items():
                devices[device] = devices.get(device, 0.0) + float(coefficient) * float(weight)
        devices = {name: value for name, value in devices.items() if abs(value) > 1.0e-12}
        derived.append(
            KnobConfig(
                name=f"{config.section.id}_model_mode_{mode_index + 1}",
                devices=devices,
                scan_step=min(knob.scan_step for knob in config.knobs),
                limit=min(knob.limit for knob in config.knobs),
            )
        )
    return tuple(derived)


def _progress(callback: ProgressCallback | None, stage: str, current: int, total: int) -> None:
    if callback is not None:
        callback(stage, current, total)
