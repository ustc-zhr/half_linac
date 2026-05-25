from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import epics.ca
from epics import caput, caput_many

from half_linac.src.shared.machine_profile.models import MachineProfile, MachineProfileError
from half_linac.src.shared.machine_profile.resolver import (
    get_workflow,
    list_elements,
    resolve_channel,
)

from .parser import _load_bpm_centroids_from_sdds, _load_watch_image_from_sdds


EPICS_CONNECTION_TIMEOUT_S = 0.5
EPICS_PUT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class VmBpmPublishSpec:
    element_id: str
    x_pv: str
    y_pv: str


@dataclass(frozen=True)
class VmWatchImagePublishSpec:
    source_watch_id: str
    target_element_id: str
    logical_channel: str
    pv_name: str
    pixel_shape: tuple[int, int]
    pixel_width_mm: float


@dataclass(frozen=True)
class VmPublishPlan:
    bpm_specs: tuple[VmBpmPublishSpec, ...] = ()
    watch_image_specs: tuple[VmWatchImagePublishSpec, ...] = ()
    watch_scalar_specs: tuple[object, ...] = field(default_factory=tuple)


def build_vm_publish_plan(profile: MachineProfile) -> VmPublishPlan:
    bpm_specs: list[VmBpmPublishSpec] = []
    for element in list_elements(profile, kind="bpm"):
        bpm_specs.append(
            VmBpmPublishSpec(
                element_id=element.id,
                x_pv=resolve_channel(profile, element.id, "x", "vm"),
                y_pv=resolve_channel(profile, element.id, "y", "vm"),
            )
        )

    watch_specs: list[VmWatchImagePublishSpec] = []
    if "beam_monitor" in profile.workflows:
        watch_specs.extend(_build_beam_monitor_watch_specs(profile))
    if "energy_spectrum" in profile.workflows:
        watch_specs.append(_build_energy_spectrum_watch_spec(profile))

    return VmPublishPlan(
        bpm_specs=tuple(bpm_specs),
        watch_image_specs=tuple(watch_specs),
    )


class VmPublisher:
    def publish_bpms(self, plan: VmPublishPlan, bpmcen_path: str | Path) -> bool:
        bpm = _load_bpm_centroids_from_sdds(Path(bpmcen_path))
        spec_by_id = {spec.element_id: spec for spec in plan.bpm_specs}

        x_channels: list[str] = []
        y_channels: list[str] = []
        x_values: list[float] = []
        y_values: list[float] = []
        for element_id, data in bpm.items():
            spec = spec_by_id.get(element_id)
            if spec is None:
                continue
            x_channels.append(spec.x_pv)
            y_channels.append(spec.y_pv)
            x_values.append(data["Cx"])
            y_values.append(data["Cy"])

        x_ok = self._publish_many_best_effort("BPM X", x_channels, x_values)
        y_ok = self._publish_many_best_effort("BPM Y", y_channels, y_values)
        return x_ok and y_ok

    def publish_watch_images(
        self,
        plan: VmPublishPlan,
        *,
        lattice: Mapping[str, Mapping[str, Any]],
        usedline: Sequence[str],
        elegant_dir: str | Path,
    ) -> bool:
        elegant_dir = Path(elegant_dir)
        usedline_set = set(usedline)
        all_ok = True

        for spec in plan.watch_image_specs:
            element = lattice.get(spec.source_watch_id)
            if element is None:
                print(
                    "flag publish skipped for "
                    f"{spec.target_element_id}/{spec.logical_channel}: "
                    f"watch {spec.source_watch_id} not found in runtime lattice."
                )
                all_ok = False
                continue

            if spec.source_watch_id not in usedline_set:
                continue

            if element.get("TYPE") != "WATCH":
                print(
                    "flag publish skipped for "
                    f"{spec.target_element_id}/{spec.logical_channel}: "
                    f"{spec.source_watch_id} is not a WATCH element."
                )
                all_ok = False
                continue

            if str(element.get("MODE", "")).lower() != "coord":
                continue
            if str(element.get("DISABLE", "0")) != "0":
                continue

            watch_output_path = elegant_dir / f"{spec.source_watch_id}.out"
            if not watch_output_path.is_file():
                print(
                    "flag publish skipped for "
                    f"{spec.target_element_id}/{spec.logical_channel}: "
                    f"missing watch output {watch_output_path.name}."
                )
                all_ok = False
                continue

            try:
                image = _load_watch_image_from_sdds(
                    watch_output_path,
                    pixel_shape=spec.pixel_shape,
                    pixel_width_mm=spec.pixel_width_mm,
                )
            except Exception as exc:
                print(
                    "flag publish skipped for "
                    f"{spec.target_element_id}/{spec.logical_channel}: {exc}"
                )
                all_ok = False
                continue

            if not self._publish_one_best_effort(
                label=f"flag {spec.target_element_id}/{spec.logical_channel}",
                pv_name=spec.pv_name,
                value=image,
            ):
                all_ok = False

        return all_ok

    def _publish_many_best_effort(self, label: str, pv_names, values) -> bool:
        if not pv_names:
            return True

        try:
            results = caput_many(
                pv_names,
                values,
                wait=False,
                connection_timeout=EPICS_CONNECTION_TIMEOUT_S,
                put_timeout=EPICS_PUT_TIMEOUT_S,
            )
        except epics.ca.ChannelAccessException as exc:
            print(f"{label} publish skipped: {exc}")
            return False
        except Exception as exc:
            print(f"{label} publish skipped: {exc}")
            return False

        failures = 0
        if results is not None:
            failures = sum(1 for result in results if result in (None, False))

        if failures:
            print(f"{label} publish incomplete: {failures}/{len(pv_names)} PV writes were not confirmed.")
            return False

        return True

    def _publish_one_best_effort(self, *, label: str, pv_name: str, value) -> bool:
        try:
            result = caput(
                pv_name,
                value,
                wait=False,
                connection_timeout=EPICS_CONNECTION_TIMEOUT_S,
                timeout=EPICS_PUT_TIMEOUT_S,
            )
        except epics.ca.ChannelAccessException as exc:
            print(f"{label} publish skipped: {exc}")
            return False
        except Exception as exc:
            print(f"{label} publish skipped: {exc}")
            return False

        if result in (None, False):
            print(f"{label} publish incomplete: PV write was not confirmed for {pv_name}.")
            return False
        return True


def _build_beam_monitor_watch_specs(profile: MachineProfile) -> list[VmWatchImagePublishSpec]:
    workflow = get_workflow(profile, "beam_monitor")
    pixel_shape = _require_backend_pixel_shape(workflow, "workflows.beam_monitor", "vm")
    pixel_width_mm = _require_backend_pixel_width(workflow, "workflows.beam_monitor", "vm")

    specs: list[VmWatchImagePublishSpec] = []
    for element in list_elements(profile, kind="flag", logical_channel="image"):
        specs.append(
            VmWatchImagePublishSpec(
                source_watch_id=element.id,
                target_element_id=element.id,
                logical_channel="image",
                pv_name=resolve_channel(profile, element.id, "image", "vm"),
                pixel_shape=pixel_shape,
                pixel_width_mm=pixel_width_mm,
            )
        )
    return specs


def _build_energy_spectrum_watch_spec(profile: MachineProfile) -> VmWatchImagePublishSpec:
    workflow = get_workflow(profile, "energy_spectrum")
    flag_element = _require_non_empty_string(
        workflow.get("flag_element"),
        "workflows.energy_spectrum.flag_element",
    )
    logical_channel = _require_non_empty_string(
        workflow.get("flag_image_channel"),
        "workflows.energy_spectrum.flag_image_channel",
    )
    source_watch_id = _require_non_empty_string(
        workflow.get("vm_watch_element"),
        "workflows.energy_spectrum.vm_watch_element",
    )
    return VmWatchImagePublishSpec(
        source_watch_id=source_watch_id,
        target_element_id=flag_element,
        logical_channel=logical_channel,
        pv_name=resolve_channel(profile, flag_element, logical_channel, "vm"),
        pixel_shape=_require_backend_pixel_shape(workflow, "workflows.energy_spectrum", "vm"),
        pixel_width_mm=_require_backend_pixel_width(workflow, "workflows.energy_spectrum", "vm"),
    )


def _require_backend_pixel_shape(
    workflow: Mapping[str, object],
    workflow_path: str,
    backend_name: str,
) -> tuple[int, int]:
    shape_by_backend = workflow.get("flag_pixel_shape")
    if not isinstance(shape_by_backend, Mapping):
        raise MachineProfileError(f"{workflow_path}.flag_pixel_shape must be a mapping.")
    shape = shape_by_backend.get(backend_name)
    if not isinstance(shape, list) or len(shape) != 2:
        raise MachineProfileError(
            f"{workflow_path}.flag_pixel_shape.{backend_name} must be [nx, ny]."
        )
    return (int(shape[0]), int(shape[1]))


def _require_backend_pixel_width(
    workflow: Mapping[str, object],
    workflow_path: str,
    backend_name: str,
) -> float:
    width_by_backend = workflow.get("flag_pixel_width_mm")
    if not isinstance(width_by_backend, Mapping):
        raise MachineProfileError(f"{workflow_path}.flag_pixel_width_mm must be a mapping.")
    width = width_by_backend.get(backend_name)
    if width is None:
        raise MachineProfileError(
            f"{workflow_path}.flag_pixel_width_mm is missing backend {backend_name!r}."
        )
    try:
        return float(width)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(
            f"{workflow_path}.flag_pixel_width_mm.{backend_name} must be numeric."
        ) from exc


def _require_non_empty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MachineProfileError(f"{path} must be a non-empty string.")
    return value.strip()


__all__ = [
    "VmBpmPublishSpec",
    "VmPublisher",
    "VmPublishPlan",
    "VmWatchImagePublishSpec",
    "build_vm_publish_plan",
]
