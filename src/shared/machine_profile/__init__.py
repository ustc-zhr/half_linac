from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class MachineProfileError(ValueError):
    """Raised when a machine profile is missing or invalid."""


@dataclass(frozen=True)
class MachineConfig:
    id: str
    family: str
    display_name: str
    default_mode: str


@dataclass(frozen=True)
class ElementConfig:
    id: str
    kind: str
    display_name: str
    order: int
    tags: tuple[str, ...]
    limits: Mapping[str, Any]
    channels: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class MachineProfile:
    schema_version: str
    machine: MachineConfig
    elements: tuple[ElementConfig, ...]
    workflows: Mapping[str, Any]
    _elements_by_id: Mapping[str, ElementConfig]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MachineProfile":
        if not isinstance(data, Mapping):
            raise MachineProfileError(
                f"Machine profile must be a mapping, got {type(data).__name__}."
            )

        required_sections = ("schema_version", "machine", "elements", "workflows")
        missing_sections = [name for name in required_sections if name not in data]
        if missing_sections:
            raise MachineProfileError(
                f"Machine profile is missing required sections: {', '.join(missing_sections)}."
            )

        schema_version = str(data["schema_version"])

        machine_raw = _expect_mapping(data["machine"], "machine")
        machine = MachineConfig(
            id=_expect_non_empty_string(machine_raw.get("id"), "machine.id"),
            family=_expect_non_empty_string(machine_raw.get("family"), "machine.family"),
            display_name=_expect_non_empty_string(
                machine_raw.get("display_name"), "machine.display_name"
            ),
            default_mode=_normalize_mode(machine_raw.get("default_mode"), "machine.default_mode"),
        )

        elements_raw = data["elements"]
        if not isinstance(elements_raw, list) or not elements_raw:
            raise MachineProfileError("elements must be a non-empty list.")

        elements: list[ElementConfig] = []
        elements_by_id: dict[str, ElementConfig] = {}
        for index, raw_element in enumerate(elements_raw):
            element = _parse_element(raw_element, index)
            if element.id in elements_by_id:
                raise MachineProfileError(f"Duplicate element.id: {element.id}")
            elements.append(element)
            elements_by_id[element.id] = element

        workflows = _expect_mapping(data["workflows"], "workflows")
        _validate_orbit_workflow(workflows.get("orbit"), elements_by_id)
        _validate_bba_workflow(workflows.get("bba"), elements_by_id)
        _validate_emit_measure_workflow(workflows.get("emit_measure"), elements_by_id)

        return cls(
            schema_version=schema_version,
            machine=machine,
            elements=tuple(sorted(elements, key=lambda elem: (elem.order, elem.id))),
            workflows=workflows,
            _elements_by_id=elements_by_id,
        )

    def get_element(self, element_id: str) -> ElementConfig:
        try:
            return self._elements_by_id[element_id]
        except KeyError as exc:
            raise MachineProfileError(f"Unknown element id: {element_id}") from exc


def load_profile(machine_id: str = "half") -> MachineProfile:
    profile_id = machine_id or "half"
    profile_path = _repo_root() / "configs" / "machines" / profile_id / "profile.json"
    if not profile_path.is_file():
        raise MachineProfileError(f"Machine profile not found: {profile_path}")

    with profile_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return MachineProfile.from_dict(raw)


def resolve_channel(
    profile: MachineProfile,
    element_id: str,
    logical_channel: str,
    mode: str | None = None,
) -> str:
    element = profile.get_element(element_id)
    try:
        channel_modes = element.channels[logical_channel]
    except KeyError as exc:
        raise MachineProfileError(
            f"Element {element_id} does not define logical channel {logical_channel!r}."
        ) from exc

    normalized_mode = _normalize_mode(mode or profile.machine.default_mode, "mode")
    try:
        pv_name = channel_modes[normalized_mode]
    except KeyError as exc:
        raise MachineProfileError(
            f"Element {element_id} channel {logical_channel!r} is missing {normalized_mode!r} mapping."
        ) from exc
    if not pv_name:
        raise MachineProfileError(
            f"Element {element_id} channel {logical_channel!r} has an empty {normalized_mode!r} PV."
        )
    return pv_name


def list_elements(profile: MachineProfile, kind: str) -> list[ElementConfig]:
    return [element for element in profile.elements if element.kind == kind]


def get_workflow(profile: MachineProfile, workflow_name: str) -> Mapping[str, Any]:
    workflows = _expect_mapping(profile.workflows, "workflows")
    try:
        workflow = workflows[workflow_name]
    except KeyError as exc:
        raise MachineProfileError(f"Workflow {workflow_name!r} is not defined.") from exc
    return _expect_mapping(workflow, f"workflows.{workflow_name}")


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "repo_bootstrap.py").is_file():
            return parent
    raise MachineProfileError("Could not locate repo root from machine_profile package.")


def _parse_element(raw_element: Any, index: int) -> ElementConfig:
    location = f"elements[{index}]"
    element_raw = _expect_mapping(raw_element, location)
    channels_raw = _expect_mapping(element_raw.get("channels"), f"{location}.channels")
    if not channels_raw:
        raise MachineProfileError(f"{location}.channels must not be empty.")

    channels: dict[str, dict[str, str]] = {}
    for logical_channel, raw_modes in channels_raw.items():
        channel_name = _expect_non_empty_string(logical_channel, f"{location}.channels key")
        modes_raw = _expect_mapping(raw_modes, f"{location}.channels.{channel_name}")
        vm_pv = _expect_non_empty_string(modes_raw.get("vm"), f"{location}.channels.{channel_name}.vm")
        real_pv = _expect_non_empty_string(
            modes_raw.get("real"), f"{location}.channels.{channel_name}.real"
        )
        channels[channel_name] = {"vm": vm_pv, "real": real_pv}

    tags_raw = element_raw.get("tags", [])
    if not isinstance(tags_raw, list):
        raise MachineProfileError(f"{location}.tags must be a list.")
    tags = tuple(str(tag) for tag in tags_raw)

    limits_raw = element_raw.get("limits", {})
    if not isinstance(limits_raw, Mapping):
        raise MachineProfileError(f"{location}.limits must be a mapping.")

    order = element_raw.get("order")
    if not isinstance(order, int):
        raise MachineProfileError(f"{location}.order must be an integer.")

    return ElementConfig(
        id=_expect_non_empty_string(element_raw.get("id"), f"{location}.id"),
        kind=_expect_non_empty_string(element_raw.get("kind"), f"{location}.kind"),
        display_name=_expect_non_empty_string(
            element_raw.get("display_name"), f"{location}.display_name"
        ),
        order=order,
        tags=tags,
        limits=dict(limits_raw),
        channels=channels,
    )


def _validate_orbit_workflow(raw_workflow: Any, elements_by_id: Mapping[str, ElementConfig]) -> None:
    workflow = _expect_mapping(raw_workflow, "workflows.orbit")
    bpms = _expect_string_list(workflow.get("bpms"), "workflows.orbit.bpms")
    xcors = _expect_string_list(workflow.get("xcors"), "workflows.orbit.xcors")
    ycors = _expect_string_list(workflow.get("ycors"), "workflows.orbit.ycors")
    lengths = {len(bpms), len(xcors), len(ycors)}
    if len(lengths) != 1:
        raise MachineProfileError(
            "workflows.orbit bpms/xcors/ycors must have the same length."
        )
    _validate_element_refs(bpms, elements_by_id, "workflows.orbit.bpms", expected_kind="bpm")
    _validate_element_refs(xcors, elements_by_id, "workflows.orbit.xcors", expected_kind="corr")
    _validate_element_refs(ycors, elements_by_id, "workflows.orbit.ycors", expected_kind="corr")


def _validate_bba_workflow(raw_workflow: Any, elements_by_id: Mapping[str, ElementConfig]) -> None:
    workflow = _expect_mapping(raw_workflow, "workflows.bba")
    presets = _expect_list(workflow.get("presets"), "workflows.bba.presets")
    _validate_unique_ids(presets, "workflows.bba.presets")
    for index, raw_preset in enumerate(presets):
        location = f"workflows.bba.presets[{index}]"
        preset = _expect_mapping(raw_preset, location)
        _expect_non_empty_string(preset.get("id"), f"{location}.id")
        _expect_non_empty_string(preset.get("plane"), f"{location}.plane")
        _validate_element_ref(preset.get("quad"), elements_by_id, f"{location}.quad", expected_kind="quad")
        _validate_element_ref(preset.get("corr"), elements_by_id, f"{location}.corr", expected_kind="corr")
        _validate_element_ref(preset.get("bpm1"), elements_by_id, f"{location}.bpm1", expected_kind="bpm")
        _validate_element_ref(preset.get("bpm2"), elements_by_id, f"{location}.bpm2", expected_kind="bpm")

    standard = _expect_mapping(workflow.get("standard"), "workflows.bba.standard")
    _validate_element_refs(
        _expect_string_list(standard.get("correctors"), "workflows.bba.standard.correctors"),
        elements_by_id,
        "workflows.bba.standard.correctors",
        expected_kind="corr",
    )
    _validate_element_refs(
        _expect_string_list(standard.get("quads"), "workflows.bba.standard.quads"),
        elements_by_id,
        "workflows.bba.standard.quads",
        expected_kind="quad",
    )
    _validate_element_refs(
        _expect_string_list(standard.get("bpm1"), "workflows.bba.standard.bpm1"),
        elements_by_id,
        "workflows.bba.standard.bpm1",
        expected_kind="bpm",
    )
    _validate_element_refs(
        _expect_string_list(standard.get("bpm2"), "workflows.bba.standard.bpm2"),
        elements_by_id,
        "workflows.bba.standard.bpm2",
        expected_kind="bpm",
    )
    _validate_preset_ref(standard.get("default_preset"), presets, "workflows.bba.standard.default_preset")

    bba2 = _expect_mapping(workflow.get("bba2"), "workflows.bba.bba2")
    _validate_element_refs(
        _expect_string_list(bba2.get("quads"), "workflows.bba.bba2.quads"),
        elements_by_id,
        "workflows.bba.bba2.quads",
        expected_kind="quad",
    )
    _validate_element_refs(
        _expect_string_list(bba2.get("correctors"), "workflows.bba.bba2.correctors"),
        elements_by_id,
        "workflows.bba.bba2.correctors",
        expected_kind="corr",
    )
    _validate_element_refs(
        _expect_string_list(bba2.get("bpm1"), "workflows.bba.bba2.bpm1"),
        elements_by_id,
        "workflows.bba.bba2.bpm1",
        expected_kind="bpm",
    )
    _validate_element_refs(
        _expect_string_list(bba2.get("bpm2"), "workflows.bba.bba2.bpm2"),
        elements_by_id,
        "workflows.bba.bba2.bpm2",
        expected_kind="bpm",
    )
    _expect_string_list(bba2.get("modes"), "workflows.bba.bba2.modes")
    _validate_preset_ref(bba2.get("default_preset"), presets, "workflows.bba.bba2.default_preset")


def _validate_emit_measure_workflow(
    raw_workflow: Any,
    elements_by_id: Mapping[str, ElementConfig],
) -> None:
    workflow = _expect_mapping(raw_workflow, "workflows.emit_measure")
    presets = _expect_list(workflow.get("presets"), "workflows.emit_measure.presets")
    _validate_unique_ids(presets, "workflows.emit_measure.presets")
    for index, raw_preset in enumerate(presets):
        location = f"workflows.emit_measure.presets[{index}]"
        preset = _expect_mapping(raw_preset, location)
        _expect_non_empty_string(preset.get("id"), f"{location}.id")
        _validate_element_ref(preset.get("quad"), elements_by_id, f"{location}.quad", expected_kind="quad")
        _validate_element_ref(preset.get("flag"), elements_by_id, f"{location}.flag", expected_kind="flag")
        energy = preset.get("energy_mev")
        if not isinstance(energy, (int, float)) or energy <= 0:
            raise MachineProfileError(f"{location}.energy_mev must be a positive number.")

    _validate_preset_ref(
        workflow.get("default_preset"), presets, "workflows.emit_measure.default_preset"
    )
    _validate_element_refs(
        _expect_string_list(workflow.get("twiss_quads"), "workflows.emit_measure.twiss_quads"),
        elements_by_id,
        "workflows.emit_measure.twiss_quads",
        expected_kind="quad",
    )


def _validate_unique_ids(items: list[Any], location: str) -> None:
    seen: set[str] = set()
    for index, raw_item in enumerate(items):
        item = _expect_mapping(raw_item, f"{location}[{index}]")
        item_id = _expect_non_empty_string(item.get("id"), f"{location}[{index}].id")
        if item_id in seen:
            raise MachineProfileError(f"Duplicate id {item_id!r} in {location}.")
        seen.add(item_id)


def _validate_preset_ref(preset_id: Any, presets: list[Any], location: str) -> None:
    required_id = _expect_non_empty_string(preset_id, location)
    preset_ids = {
        _expect_non_empty_string(_expect_mapping(item, location).get("id"), f"{location}.id")
        for item in presets
    }
    if required_id not in preset_ids:
        raise MachineProfileError(f"{location} references unknown preset {required_id!r}.")


def _validate_element_refs(
    refs: list[str],
    elements_by_id: Mapping[str, ElementConfig],
    location: str,
    expected_kind: str | None = None,
) -> None:
    for ref in refs:
        _validate_element_ref(ref, elements_by_id, location, expected_kind=expected_kind)


def _validate_element_ref(
    ref: Any,
    elements_by_id: Mapping[str, ElementConfig],
    location: str,
    expected_kind: str | None = None,
) -> None:
    element_id = _expect_non_empty_string(ref, location)
    try:
        element = elements_by_id[element_id]
    except KeyError as exc:
        raise MachineProfileError(f"{location} references unknown element {element_id!r}.") from exc
    if expected_kind is not None and element.kind != expected_kind:
        raise MachineProfileError(
            f"{location} expected {expected_kind!r} element {element_id!r}, got {element.kind!r}."
        )


def _expect_mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MachineProfileError(f"{location} must be a mapping.")
    return value


def _expect_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise MachineProfileError(f"{location} must be a list.")
    return value


def _expect_string_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MachineProfileError(f"{location} must be a non-empty list of strings.")
    items: list[str] = []
    for index, item in enumerate(value):
        items.append(_expect_non_empty_string(item, f"{location}[{index}]"))
    return items


def _expect_non_empty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MachineProfileError(f"{location} must be a non-empty string.")
    return value.strip()


def _normalize_mode(value: Any, location: str) -> str:
    text = _expect_non_empty_string(value, location).lower().replace("_", " ").strip()
    if text in {"vm", "virtual machine"}:
        return "vm"
    if text in {"real", "real machine"}:
        return "real"
    raise MachineProfileError(f"{location} must resolve to 'vm' or 'real', got {value!r}.")


__all__ = [
    "ElementConfig",
    "MachineConfig",
    "MachineProfile",
    "MachineProfileError",
    "get_workflow",
    "list_elements",
    "load_profile",
    "resolve_channel",
]
