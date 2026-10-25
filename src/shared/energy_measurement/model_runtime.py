"""Model-backed setup helpers for three-BPM energy reconstruction."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from half_linac.src.shared.machine_profile.model_backend import build_model_backend
from half_linac.src.shared.machine_profile.models import AppContext, MachineProfileError

from .bpm_transport import BpmTransportModel


def discover_bpm_transport_segment(
    context: AppContext,
    bpm_ids: Sequence[str],
    *,
    backend: object | None = None,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Select the shortest ordered model path and infer its quadrupoles."""
    ordered_bpms = tuple(str(value) for value in bpm_ids)
    if len(ordered_bpms) != 3 or len(set(ordered_bpms)) != 3:
        raise MachineProfileError("BPM transport requires three distinct BPMs.")

    discovery_backend = backend or build_model_backend(context)
    selected_line = discovery_backend.get_measurement_model_line(
        ordered_bpms[0], ordered_bpms[2]
    )
    line_backend = (
        discovery_backend
        if backend is not None
        else build_model_backend(context, line_name=selected_line.name)
    )
    path = tuple(line_backend.get_line_elements(ordered_bpms[0], ordered_bpms[2]))
    path_ids = tuple(str(element["NAME"]) for element in path)
    try:
        indices = tuple(path_ids.index(bpm_id) for bpm_id in ordered_bpms)
    except ValueError as exc:
        raise MachineProfileError(
            f"Model line {selected_line.name!r} does not contain all configured BPMs."
        ) from exc
    if indices != tuple(sorted(indices)) or len(set(indices)) != 3:
        raise MachineProfileError(
            f"Configured BPM order {ordered_bpms!r} is invalid on model line "
            f"{selected_line.name!r}."
        )

    quadrupoles: list[str] = []
    for element_id in path_ids:
        try:
            profile_element = context.profile.get_element(element_id)
        except MachineProfileError:
            continue
        if profile_element.kind == "quad":
            quadrupoles.append(element_id)
    if not quadrupoles:
        raise MachineProfileError(
            f"No quadrupoles were found between {ordered_bpms[0]} and "
            f"{ordered_bpms[2]}."
        )
    return selected_line.name, path_ids, tuple(quadrupoles)


def build_bpm_transport_model(
    context: AppContext,
    source: Mapping[str, Any],
    reference_energy_mev: float,
    k1_values_per_m2: Mapping[str, float],
    *,
    backend_factory: Callable[..., object] = build_model_backend,
) -> BpmTransportModel:
    """Run both exit-to-exit maps with live K1_total values as K1 overrides."""
    backend = backend_factory(
        context,
        energy_mev=float(reference_energy_mev),
        line_name=str(source["model_line"]),
    )
    overrides = {
        quadrupole: {"K1": float(k1_values_per_m2[quadrupole])}
        for quadrupole in source["quadrupoles"]
    }
    bpm1, bpm2, bpm3 = source["bpm_ids"]
    m1 = backend.get_map(
        bpm1, bpm2, lattice_overrides=overrides, seq="exit2exit"
    )
    m2 = backend.get_map(
        bpm2, bpm3, lattice_overrides=overrides, seq="exit2exit"
    )
    return BpmTransportModel.from_matrices(
        source["bpm_ids"],
        source["offsets_mm"],
        m1,
        m2,
        min_abs_r12_m=float(source["model"]["min_abs_r12_m"]),
        min_abs_r16_m=float(source["model"]["min_abs_r16_m"]),
    )
