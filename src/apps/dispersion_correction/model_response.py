from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from half_linac.src.apps.dispersion_correction.models import (
    ModelObservableConfig,
    ModelOpticsCurve,
    ModelResponseResult,
    RunConfig,
)
from half_linac.src.shared.machine_profile import (
    MODEL_SNAPSHOT_SOURCE_DESIGN,
    AppContext,
    build_model_backend,
    build_model_snapshot,
)


ProgressCallback = Callable[[str, int, int], None]


def calculate_model_response(
    context: AppContext,
    config: RunConfig,
    *,
    model_source: str = MODEL_SNAPSHOT_SOURCE_DESIGN,
    pv_reader: Callable[[str], Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> ModelResponseResult:
    """Compare selected optics with a read-only design-reference prediction."""

    entrance = config.section.model_entrance
    exit_element = config.section.model_exit
    observables = config.section.model_observables
    if not entrance or not exit_element:
        raise ValueError("Model comparison requires section.model_entrance and model_exit")
    if not observables:
        raise ValueError("Model comparison requires section.model_observables")
    if context.model_backend is None:
        raise ValueError("The selected machine profile has no model backend")

    backend = build_model_backend(context)
    correction_devices = _correction_devices(config)
    design_k1 = _design_k1_values(backend, correction_devices)
    snapshot = None
    selected_overrides: dict[str, dict[str, float]] = {}
    source_name = str(model_source).strip().lower().replace("-", "_")
    _progress(progress_callback, "Calculating design reference", 0, 3)
    design_curve = _optics_curve(backend, entrance, exit_element)
    if source_name not in {"design", "lattice"}:
        _progress(progress_callback, "Reading current K1 values", 1, 3)
        snapshot = build_model_snapshot(
            context,
            _section_quadrupole_fields(backend, entrance, exit_element),
            source=model_source,
            pv_reader=pv_reader,
        )
        source_name = snapshot.source
        selected_overrides = snapshot.lattice_overrides
    else:
        source_name = MODEL_SNAPSHOT_SOURCE_DESIGN

    if selected_overrides:
        _progress(progress_callback, "Calculating current K1 model optics", 2, 3)
        selected_curve = _optics_curve(
            backend,
            entrance,
            exit_element,
            lattice_overrides=selected_overrides,
        )
        if correction_devices:
            design_reference_overrides = _merge_lattice_overrides(
                selected_overrides,
                {
                    device: {"K1": design_k1[device]}
                    for device in correction_devices
                },
            )
            _progress(
                progress_callback,
                "Calculating design-reference optics",
                2,
                3,
            )
            design_reference_curve = _optics_curve(
                backend,
                entrance,
                exit_element,
                lattice_overrides=design_reference_overrides,
            )
        else:
            design_reference_curve = design_curve
        selected_k1 = {
            device: float(selected_overrides[device]["K1"])
            for device in correction_devices
        }
    else:
        selected_curve = design_curve
        design_reference_curve = design_curve
        selected_k1 = dict(design_k1)

    selected = _observable_vector(selected_curve, observables)
    design_reference = _observable_vector(design_reference_curve, observables)
    target = np.asarray([observable.target for observable in observables], dtype=float)
    design_reference_deltas = {
        device: design_k1[device] - selected_k1[device]
        for device in correction_devices
    }
    _progress(progress_callback, "Model comparison complete", 3, 3)
    return ModelResponseResult(
        section_id=config.section.id,
        observable_names=tuple(item.name for item in observables),
        observable_elements=tuple(item.element for item in observables),
        observable_components=tuple(item.component for item in observables),
        observable_units=tuple(item.unit for item in observables),
        device_names=correction_devices,
        selected_values=selected,
        target_values=target,
        design_reference_values=design_reference,
        selected_curve=selected_curve,
        design_reference_curve=design_reference_curve,
        selected_k1=selected_k1,
        design_k1=design_k1,
        design_reference_deltas=design_reference_deltas,
        model_source=source_name,
        design_curve=design_curve,
        snapshot_metadata=snapshot.as_metadata() if snapshot is not None else None,
        entrance_condition=f"D=D'=0 assumed at {entrance}",
    )


def model_response_to_dict(result: ModelResponseResult) -> dict[str, Any]:
    return {
        "section_id": result.section_id,
        "model_source": result.model_source,
        "entrance_condition": result.entrance_condition,
        "model_snapshot": result.snapshot_metadata,
        "observables": [
            {
                "name": result.observable_names[index],
                "element": result.observable_elements[index],
                "component": result.observable_components[index],
                "unit": result.observable_units[index],
                "selected": float(result.selected_values[index]),
                "target": float(result.target_values[index]),
                "design_reference": float(result.design_reference_values[index]),
            }
            for index in range(len(result.observable_names))
        ],
        "quadrupole_design_reference": [
            {
                "device": device,
                "selected_k1": result.selected_k1[device],
                "design_k1": result.design_k1[device],
                "design_reference_delta_k1": result.design_reference_deltas[device],
            }
            for device in result.device_names
        ],
        "selected_rms": result.selected_rms,
        "design_reference_rms": result.design_reference_rms,
        "selected_curve": _curve_to_dict(result.selected_curve),
        "design_reference_curve": _curve_to_dict(result.design_reference_curve),
        "design_curve": _curve_to_dict(result.design_curve),
    }


def format_model_response(result: ModelResponseResult) -> str:
    lines = [
        f"Model design comparison: {result.section_id}",
        f"Model source: {_model_source_label(result.model_source)}",
        f"Entrance condition: {result.entrance_condition or 'not specified'}",
        "Machine writes: none",
        "",
        "Observable selected / target / design-reference prediction:",
    ]
    for index, name in enumerate(result.observable_names):
        unit = result.observable_units[index]
        lines.append(
            f"  {name}: {result.selected_values[index]:.8g} / "
            f"{result.target_values[index]:.8g} / "
            f"{result.design_reference_values[index]:.8g} {unit}"
        )
    lines.extend(
        [
            f"  RMS: {result.selected_rms:.8g} -> {result.design_reference_rms:.8g}",
            "",
            "Quadrupole design reference (K1 in 1/m^2):",
        ]
    )
    for device in result.device_names:
        lines.append(
            f"  {device}: selected {result.selected_k1[device]:.8g}, "
            f"design {result.design_k1[device]:.8g}, "
            f"design-reference delta {result.design_reference_deltas[device]:+.8g}"
        )
    lines.extend(
        [
            "",
            "Optics envelope (selected -> design-reference prediction):",
            f"  max beta_x: {np.max(result.selected_curve.beta_x_m):.8g} -> "
            f"{np.max(result.design_reference_curve.beta_x_m):.8g} m",
            f"  max beta_y: {np.max(result.selected_curve.beta_y_m):.8g} -> "
            f"{np.max(result.design_reference_curve.beta_y_m):.8g} m",
            "",
            "Note: the design-reference delta is not a beam-based correction recommendation.",
        ]
    )
    if result.snapshot_metadata is not None:
        lines.extend(["", "Current K1 model input:"])
        created_at = result.snapshot_metadata.get("created_at")
        if created_at:
            lines.append(f"  captured: {created_at}")
        for field in result.snapshot_metadata.get("fields", []):
            lines.append(
                f"  {field['element_id']}.{field['field_name']} = {field['value']:.8g}"
                f"  [{field.get('source_pv') or 'design'}]"
            )
    return "\n".join(lines) + "\n"


def _model_source_label(source: str) -> str:
    labels = {
        "design": "Design lattice",
        "live_from_vm": "Current K1 model (VM backend)",
        "live_from_real": "Current K1 model (REAL backend)",
    }
    return labels.get(str(source), str(source))


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


def _correction_devices(config: RunConfig) -> tuple[str, ...]:
    devices = []
    seen = set()
    for knob in config.knobs:
        for device in knob.devices:
            if device not in seen:
                devices.append(device)
                seen.add(device)
    if not devices and not config.section.diagnostic_only:
        raise ValueError("Model design comparison requires at least one quadrupole")
    return tuple(sorted(devices))


def _design_k1_values(
    backend,
    devices: tuple[str, ...],
) -> dict[str, float]:
    values: dict[str, float] = {}
    for device in devices:
        element = backend.get_lattice_element(device)
        try:
            values[device] = float(element["K1"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Model quadrupole {device} has no numeric design K1") from exc
    return values


def _section_quadrupole_fields(
    backend,
    entrance: str,
    exit_element: str,
) -> tuple[tuple[str, str], ...]:
    fields = []
    seen = set()
    for element in backend.get_line_elements(entrance, exit_element):
        element_id = str(element.get("NAME", "")).strip()
        element_type = str(element.get("TYPE", "")).strip().upper()
        if element_type != "QUAD" or "K1" not in element or not element_id:
            continue
        field = (element_id, "K1")
        if field not in seen:
            fields.append(field)
            seen.add(field)
    if not fields:
        raise ValueError(
            f"Model section {entrance} -> {exit_element} contains no K1 quadrupoles"
        )
    return tuple(fields)


def _merge_lattice_overrides(
    base: Mapping[str, Mapping[str, float]] | None,
    changes: Mapping[str, Mapping[str, float]] | None,
) -> dict[str, dict[str, float]]:
    merged = {
        str(element_id): {str(field): float(value) for field, value in values.items()}
        for element_id, values in (base or {}).items()
    }
    for element_id, values in (changes or {}).items():
        merged.setdefault(str(element_id), {}).update(
            {str(field): float(value) for field, value in values.items()}
        )
    return merged


def _optics_curve(
    backend,
    entrance: str,
    exit_element: str,
    *,
    lattice_overrides: Mapping[str, Mapping[str, float]] | None = None,
) -> ModelOpticsCurve:
    sequence = _optics_sequence(backend, entrance)
    rows = backend.get_optics_profile(
        entrance,
        exit_element,
        lattice_overrides=lattice_overrides,
        seq=sequence,
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


def _optics_sequence(backend, entrance: str) -> str:
    element = backend.get_lattice_element(entrance)
    element_type = str(element.get("TYPE", "")).strip().upper()
    try:
        length = float(element.get("L", 0.0))
    except (TypeError, ValueError):
        length = float("nan")
    if (
        element_type in {"MARK", "MONI", "WATCH"}
        and np.isfinite(length)
        and abs(length) <= 1.0e-12
    ):
        return "ent2exit"
    return "exit2exit"


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


def _progress(callback: ProgressCallback | None, stage: str, current: int, total: int) -> None:
    if callback is not None:
        callback(stage, current, total)
