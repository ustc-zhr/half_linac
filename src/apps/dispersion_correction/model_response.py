from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from half_linac.src.apps.dispersion_correction.models import (
    KnobConfig,
    ModelObservableConfig,
    ModelOpticsCurve,
    ModelResponseResult,
    RunConfig,
)
from half_linac.src.apps.dispersion_correction.solver import (
    condition_number,
    solve_bounded_correction,
)
from half_linac.src.shared.machine_profile import AppContext, build_model_backend


ProgressCallback = Callable[[str, int, int], None]


def calculate_model_response(
    context: AppContext,
    config: RunConfig,
    *,
    progress_callback: ProgressCallback | None = None,
) -> ModelResponseResult:
    """Calculate a design response and correction preview without touching VM state."""

    entrance = config.section.model_entrance
    exit_element = config.section.model_exit
    observables = config.section.model_observables
    if not entrance or not exit_element:
        raise ValueError("Model response requires section.model_entrance and model_exit")
    if not observables:
        raise ValueError("Model response requires section.model_observables")
    if context.model_backend is None:
        raise ValueError("The selected machine profile has no model backend")

    backend = build_model_backend(context)
    base_k1 = _base_k1_values(backend, config)
    total = 2 + 2 * len(config.knobs)
    _progress(progress_callback, "Calculating baseline optics", 0, total)
    baseline_curve = _optics_curve(backend, entrance, exit_element)
    baseline = _observable_vector(baseline_curve, observables)
    matrix = np.zeros((len(observables), len(config.knobs)), dtype=float)

    completed = 1
    for column, knob in enumerate(config.knobs):
        _progress(progress_callback, f"{knob.name} +scan", completed, total)
        plus_curve = _optics_curve(
            backend,
            entrance,
            exit_element,
            lattice_overrides=_knob_overrides(base_k1, knob, knob.scan_step),
        )
        plus = _observable_vector(plus_curve, observables)
        completed += 1
        _progress(progress_callback, f"{knob.name} -scan", completed, total)
        minus_curve = _optics_curve(
            backend,
            entrance,
            exit_element,
            lattice_overrides=_knob_overrides(base_k1, knob, -knob.scan_step),
        )
        minus = _observable_vector(minus_curve, observables)
        completed += 1
        matrix[:, column] = (plus - minus) / (2.0 * knob.scan_step)

    _, singular_values, vh = np.linalg.svd(matrix, full_matrices=False)
    largest = float(np.max(singular_values)) if singular_values.size else 0.0
    retained = (
        singular_values / largest > config.solver.svd_cut
        if largest > 0
        else np.zeros_like(singular_values, dtype=bool)
    )
    target = np.asarray([observable.target for observable in observables], dtype=float)
    deltas, _, _ = solve_bounded_correction(
        matrix,
        baseline - target,
        config.solver.svd_cut,
        1.0,
        np.asarray([knob.limit for knob in config.knobs], dtype=float),
        1.0,
        np.zeros(len(config.knobs), dtype=float),
        np.zeros(len(config.knobs), dtype=float),
        config.solver.regularization,
    )
    preview_deltas = {
        knob.name: float(delta)
        for knob, delta in zip(config.knobs, deltas)
    }
    _progress(progress_callback, "Calculating correction preview", completed, total)
    preview_curve = _optics_curve(
        backend,
        entrance,
        exit_element,
        lattice_overrides=_combined_knob_overrides(base_k1, config.knobs, deltas),
    )
    preview = _observable_vector(preview_curve, observables)
    derived_knobs = _derived_knobs(config, vh, retained)
    _progress(progress_callback, "Model response complete", total, total)
    return ModelResponseResult(
        section_id=config.section.id,
        observable_names=tuple(item.name for item in observables),
        observable_elements=tuple(item.element for item in observables),
        observable_components=tuple(item.component for item in observables),
        observable_units=tuple(item.unit for item in observables),
        knob_names=tuple(knob.name for knob in config.knobs),
        baseline_values=baseline,
        target_values=target,
        response_matrix=matrix,
        singular_values=singular_values,
        condition_number=condition_number(singular_values),
        retained_rank=int(np.count_nonzero(retained)),
        derived_knobs=derived_knobs,
        baseline_curve=baseline_curve,
        preview_knob_deltas=preview_deltas,
        preview_values=preview,
        preview_curve=preview_curve,
    )


def model_response_to_dict(result: ModelResponseResult) -> dict[str, Any]:
    return {
        "section_id": result.section_id,
        "observables": [
            {
                "name": result.observable_names[index],
                "element": result.observable_elements[index],
                "component": result.observable_components[index],
                "unit": result.observable_units[index],
                "baseline": float(result.baseline_values[index]),
                "target": float(result.target_values[index]),
                "preview": float(result.preview_values[index]),
            }
            for index in range(len(result.observable_names))
        ],
        "knob_names": list(result.knob_names),
        "response_matrix": result.response_matrix.tolist(),
        "singular_values": result.singular_values.tolist(),
        "condition_number": result.condition_number,
        "retained_rank": result.retained_rank,
        "baseline_rms": result.baseline_rms,
        "preview_rms": result.preview_rms,
        "preview_knob_deltas": dict(result.preview_knob_deltas),
        "derived_knobs": [
            {
                "name": knob.name,
                "devices": dict(knob.devices),
                "scan_step": knob.scan_step,
                "limit": knob.limit,
            }
            for knob in result.derived_knobs
        ],
        "baseline_curve": _curve_to_dict(result.baseline_curve),
        "preview_curve": _curve_to_dict(result.preview_curve),
    }


def format_model_response(result: ModelResponseResult) -> str:
    lines = [
        f"Model dispersion response: {result.section_id}",
        "",
        "Observable baseline / target / preview:",
    ]
    for index, name in enumerate(result.observable_names):
        unit = result.observable_units[index]
        lines.append(
            f"  {name}: {result.baseline_values[index]:.8g} / "
            f"{result.target_values[index]:.8g} / "
            f"{result.preview_values[index]:.8g} {unit}"
        )
    lines.extend(
        [
            f"  RMS: {result.baseline_rms:.8g} -> {result.preview_rms:.8g}",
            "",
            "Raw knob response matrix (observable unit per knob unit):",
            "  " + "  ".join(result.knob_names),
        ]
    )
    for index, name in enumerate(result.observable_names):
        values = "  ".join(f"{value:.8g}" for value in result.response_matrix[index])
        lines.append(f"  {name}: {values}")
    lines.extend(
        [
            "",
            "Singular values: " + ", ".join(f"{value:.8g}" for value in result.singular_values),
            f"Condition number: {result.condition_number:.8g}",
            f"Retained rank: {result.retained_rank}",
            "",
            "Preview raw-knob deltas:",
        ]
    )
    for name in result.knob_names:
        lines.append(f"  {name}: {result.preview_knob_deltas[name]:+.8g}")
    lines.extend(["", "Model-derived orthogonal knobs:"])
    for knob in result.derived_knobs:
        devices = ", ".join(f"{name}*{weight:.8g}" for name, weight in knob.devices.items())
        lines.append(f"  {knob.name}: {devices}")
    lines.extend(
        [
            "",
            "Optics envelope (baseline -> preview):",
            f"  max beta_x: {np.max(result.baseline_curve.beta_x_m):.8g} -> "
            f"{np.max(result.preview_curve.beta_x_m):.8g} m",
            f"  max beta_y: {np.max(result.baseline_curve.beta_y_m):.8g} -> "
            f"{np.max(result.preview_curve.beta_y_m):.8g} m",
        ]
    )
    return "\n".join(lines) + "\n"


def _curve_to_dict(curve: ModelOpticsCurve) -> dict[str, Any]:
    return {
        "element_names": list(curve.element_names),
        "element_types": list(curve.element_types),
        "element_occurrences": list(curve.element_occurrences),
        "element_lengths_m": curve.element_lengths_m.tolist(),
        "element_k1_m2": _finite_or_none(curve.element_k1_m2),
        "element_angles_rad": _finite_or_none(curve.element_angles_rad),
        "element_tilts_rad": curve.element_tilts_rad.tolist(),
        "s_m": curve.s_m.tolist(),
        "dx_mm": curve.dx_mm.tolist(),
        "dxp_mrad": curve.dxp_mrad.tolist(),
        "dy_mm": curve.dy_mm.tolist(),
        "dyp_mrad": curve.dyp_mrad.tolist(),
        "beta_x_m": curve.beta_x_m.tolist(),
        "beta_y_m": curve.beta_y_m.tolist(),
    }


def _finite_or_none(values: np.ndarray) -> list[float | None]:
    return [float(value) if np.isfinite(value) else None for value in values]


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


def _combined_knob_overrides(
    base_k1: Mapping[str, float],
    knobs: tuple[KnobConfig, ...],
    deltas: np.ndarray,
) -> dict[str, dict[str, float]]:
    changes = {device: 0.0 for device in base_k1}
    for knob, delta in zip(knobs, deltas):
        for device, weight in knob.devices.items():
            changes[device] += float(weight) * float(delta)
    return {
        device: {"K1": base_k1[device] + changes[device]}
        for device in base_k1
    }


def _optics_curve(
    backend,
    entrance: str,
    exit_element: str,
    *,
    lattice_overrides: Mapping[str, Mapping[str, float]] | None = None,
) -> ModelOpticsCurve:
    rows = backend.get_optics_profile(
        entrance,
        exit_element,
        lattice_overrides=lattice_overrides,
    )
    if not rows:
        raise ValueError("Elegant model returned an empty optics profile")
    return ModelOpticsCurve(
        element_names=tuple(str(row["element_name"]) for row in rows),
        element_types=tuple(str(row.get("element_type", "")) for row in rows),
        element_occurrences=tuple(int(row.get("element_occurrence", 1)) for row in rows),
        element_lengths_m=np.asarray(
            [row.get("element_length_m", 0.0) for row in rows], dtype=float
        ),
        element_k1_m2=np.asarray(
            [row.get("element_k1_m2", float("nan")) for row in rows], dtype=float
        ),
        element_angles_rad=np.asarray(
            [row.get("element_angle_rad", float("nan")) for row in rows], dtype=float
        ),
        element_tilts_rad=np.asarray(
            [row.get("element_tilt_rad", 0.0) for row in rows], dtype=float
        ),
        s_m=np.asarray([row["s_m"] for row in rows], dtype=float),
        dx_mm=1000.0 * np.asarray([row["dx_m"] for row in rows], dtype=float),
        dxp_mrad=1000.0 * np.asarray([row["dxp_rad"] for row in rows], dtype=float),
        dy_mm=1000.0 * np.asarray([row["dy_m"] for row in rows], dtype=float),
        dyp_mrad=1000.0 * np.asarray([row["dyp_rad"] for row in rows], dtype=float),
        beta_x_m=np.asarray([row["beta_x_m"] for row in rows], dtype=float),
        beta_y_m=np.asarray([row["beta_y_m"] for row in rows], dtype=float),
    )


def _observable_vector(
    curve: ModelOpticsCurve,
    observables: tuple[ModelObservableConfig, ...],
) -> np.ndarray:
    component_values = {
        "dx": curve.dx_mm,
        "dxp": curve.dxp_mrad,
        "dy": curve.dy_mm,
        "dyp": curve.dyp_mrad,
    }
    values = []
    for observable in observables:
        matches = [
            index
            for index, element_name in enumerate(curve.element_names)
            if element_name == observable.element
        ]
        if not matches:
            raise ValueError(
                f"Model observable {observable.name!r} element {observable.element!r} "
                "is outside the configured section"
            )
        values.append(float(component_values[observable.component][matches[-1]]))
    return np.asarray(values, dtype=float)


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
