from __future__ import annotations

from dataclasses import dataclass, field, fields
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
    plane: str | None
    roles: tuple[str, ...]
    tags: tuple[str, ...]
    limits: Mapping[str, Any]
    channels: Mapping[str, Mapping[str, str]]

    def limits_for(self, channel: str) -> Mapping[str, Any]:
        """Return limits for one logical channel, with legacy element-level fallback."""
        channel_limits = self.limits.get(channel)
        if isinstance(channel_limits, Mapping):
            return channel_limits
        if "low" in self.limits or "high" in self.limits:
            return self.limits
        return {}


@dataclass(frozen=True)
class MachineVmRuntimeConfig:
    root: str
    ui_entrypoint: str
    manager_entrypoint: str
    runtime_json: str
    bootstrap_lattice: str
    bootstrap_ele: str
    line_name: str


@dataclass(frozen=True)
class MachineSoftIocRuntimeConfig:
    root: str
    substitutions_file: str


@dataclass(frozen=True)
class MachineRuntimeConfig:
    vm: MachineVmRuntimeConfig
    softioc: MachineSoftIocRuntimeConfig


@dataclass(frozen=True)
class MachineProfile:
    schema_version: str
    machine: MachineConfig
    control_backends: tuple[str, ...]
    runtime: MachineRuntimeConfig | None
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
            default_mode=normalize_mode(machine_raw.get("default_mode"), "machine.default_mode"),
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

        control_backends = _parse_profile_control_backends(
            data.get("control_backends"),
            elements,
        )
        if machine.default_mode not in control_backends:
            raise MachineProfileError(
                "machine.default_mode must be declared in control_backends."
            )
        control_backends = _order_control_backends(control_backends, machine.default_mode)
        _validate_element_backends(elements, control_backends)

        runtime = None
        if "runtime" in data and data.get("runtime") is not None:
            runtime = _parse_machine_runtime(data.get("runtime"), "runtime")

        workflows = _expect_mapping(data["workflows"], "workflows")
        _validate_orbit_workflow(workflows.get("orbit"), elements_by_id)
        _validate_bba_workflow(workflows.get("bba"), elements_by_id)
        _validate_emit_measure_workflow(workflows.get("emit_measure"), elements_by_id)
        _validate_solenoid_centering_workflow(
            workflows.get("solenoid_centering"),
            elements_by_id,
        )

        return cls(
            schema_version=schema_version,
            machine=machine,
            control_backends=control_backends,
            runtime=runtime,
            elements=tuple(sorted(elements, key=lambda elem: (elem.order, elem.id))),
            workflows=workflows,
            _elements_by_id=elements_by_id,
        )

    def get_element(self, element_id: str) -> ElementConfig:
        try:
            return self._elements_by_id[element_id]
        except KeyError as exc:
            raise MachineProfileError(f"Unknown element id: {element_id}") from exc


@dataclass(frozen=True)
class ControlBackendConfig:
    name: str


@dataclass(frozen=True)
class ModelBackendConfig:
    name: str
    engine: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrbitWorkflowConfig:
    bpms: tuple[str, ...]
    xcors: tuple[str, ...]
    ycors: tuple[str, ...]
    default_target_bpms: tuple[str, ...] = ()


@dataclass(frozen=True)
class BBAFamilyConfig:
    name: str
    correctors: tuple[str, ...]
    quads: tuple[str, ...]
    bpm1: tuple[str, ...]
    bpm2: tuple[str, ...]
    default_preset: str
    control_backends: tuple[str, ...] = ()

    @property
    def modes(self) -> tuple[str, ...]:
        return self.control_backends


class _OptionalFieldMapping:
    def as_dict(self) -> dict[str, Any]:
        return {
            field_def.name: value
            for field_def in fields(self)
            if (value := getattr(self, field_def.name)) is not None
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, None)
        if value is None and key not in self.as_dict():
            raise KeyError(key)
        return value

    def __contains__(self, key: str) -> bool:
        return key in self.as_dict()


@dataclass(frozen=True)
class BBAScanConfig(_OptionalFieldMapping):
    corr_from: float | None = None
    corr_end: float | None = None
    corr_steps: int | None = None
    quad_from: float | None = None
    quad_end: float | None = None
    quad_steps: int | None = None
    samples: int | None = None
    settle_time: float | None = None
    sample_interval: float | None = None
    corr_unit: str | None = None
    quad_unit: str | None = None
    corr_mode: str | None = None
    quad_mode: str | None = None


@dataclass(frozen=True)
class BBAAnalysisConfig(_OptionalFieldMapping):
    energy_mev: float | None = None
    bpm1_samples: int | None = None
    by_formula: str | None = None
    bx_formula: str | None = None
    leff_by: float | None = None
    leff_bx: float | None = None
    quad_leff: float | None = None


@dataclass(frozen=True)
class BBAPreset:
    id: str
    family: str
    plane: str
    quad: str
    corr: str
    bpm1: str
    bpm2: str
    mode: str | None = None
    scan: BBAScanConfig = field(default_factory=BBAScanConfig)
    analysis: BBAAnalysisConfig = field(default_factory=BBAAnalysisConfig)

    @property
    def energy_mev(self) -> float | None:
        return self.analysis.energy_mev


@dataclass(frozen=True)
class BBAWorkflowConfig:
    presets: tuple[BBAPreset, ...]
    presets_by_id: Mapping[str, BBAPreset]
    bba1: BBAFamilyConfig
    bba2: BBAFamilyConfig


@dataclass(frozen=True)
class EmitAdaptiveScanConfig(_OptionalFieldMapping):
    k1_min: float | None = None
    k1_max: float | None = None
    initial_points: int | None = None
    target_points_per_plane: int | None = None
    max_unique_points: int | None = None
    waist_size_squared_ratio: float | None = None
    reuse_tolerance: float | None = None
    max_retries: int | None = None


@dataclass(frozen=True)
class EmitScanConfig(_OptionalFieldMapping):
    k1_from: float | None = None
    k1_end: float | None = None
    k1_steps: int | None = None
    samples: int | None = None
    settle_time: float | None = None
    sample_interval: float | None = None
    unit: str | None = None
    mode: str | None = None
    adaptive: EmitAdaptiveScanConfig | None = None


@dataclass(frozen=True)
class EmitAnalysisConfig(_OptionalFieldMapping):
    energy_mev: float | None = None


@dataclass(frozen=True)
class EmitPreset:
    id: str
    quad: str
    flag: str
    model_line: str | None = None
    scan: EmitScanConfig = field(default_factory=EmitScanConfig)
    analysis: EmitAnalysisConfig = field(default_factory=EmitAnalysisConfig)

    @property
    def energy_mev(self) -> float | None:
        return self.analysis.energy_mev


@dataclass(frozen=True)
class EmitMeasureWorkflowConfig:
    presets: tuple[EmitPreset, ...]
    presets_by_id: Mapping[str, EmitPreset]
    twiss_quads: tuple[str, ...]
    default_preset: str


@dataclass(frozen=True)
class SolenoidCenteringScanRange:
    relative_from: float
    relative_to: float
    steps: int


@dataclass(frozen=True)
class SolenoidCenteringMotionVerification:
    solenoid_readback_tolerance: float
    corrector_readback_tolerance: float
    readback_timeout_s: float
    poll_interval_s: float = 0.1


@dataclass(frozen=True)
class SolenoidCenteringPreset:
    id: str
    display_name: str
    hcorr: str
    vcorr: str
    bpm: str
    solenoid_scan: SolenoidCenteringScanRange
    corrector_scan: SolenoidCenteringScanRange
    samples_per_point: int
    settle_time_s: float
    sample_interval_s: float
    max_rounds: int
    solenoid: str | None = None
    solenoid_setpoint_pv: str | None = None
    solenoid_readback_pv: str | None = None
    motion_verification: SolenoidCenteringMotionVerification | None = None
    minimum_relative_score_improvement: float = 0.05


@dataclass(frozen=True)
class SolenoidCenteringWorkflowConfig:
    presets: tuple[SolenoidCenteringPreset, ...]
    presets_by_id: Mapping[str, SolenoidCenteringPreset]
    default_preset: str


@dataclass(frozen=True)
class AppContext:
    app_name: str
    profile: MachineProfile
    control_backend: ControlBackendConfig
    model_backend: ModelBackendConfig | None = None
    orbit_workflow: OrbitWorkflowConfig | None = None
    bba_workflow: BBAWorkflowConfig | None = None
    emit_measure_workflow: EmitMeasureWorkflowConfig | None = None
    solenoid_centering_workflow: SolenoidCenteringWorkflowConfig | None = None
    selected_preset_id: str | None = None

    @property
    def machine(self) -> MachineConfig:
        return self.profile.machine


def normalize_mode(value: Any, location: str) -> str:
    text = _expect_non_empty_string(value, location).lower().replace("_", " ").strip()
    if text in {"vm", "virtual machine"}:
        return "vm"
    if text in {"real", "real machine"}:
        return "real"
    raise MachineProfileError(f"{location} must resolve to 'vm' or 'real', got {value!r}.")


def normalize_plane(value: Any, location: str) -> str:
    text = _expect_non_empty_string(value, location).lower().strip()
    if text in {"x", "y"}:
        return text
    raise MachineProfileError(f"{location} must be 'x' or 'y', got {value!r}.")


def _parse_element(raw_element: Any, index: int) -> ElementConfig:
    location = f"elements[{index}]"
    element_raw = _expect_mapping(raw_element, location)
    element_id = _expect_non_empty_string(element_raw.get("id"), f"{location}.id")
    kind = _expect_non_empty_string(element_raw.get("kind"), f"{location}.kind")
    channels_raw = _expect_mapping(element_raw.get("channels"), f"{location}.channels")
    if not channels_raw:
        raise MachineProfileError(f"{location}.channels must not be empty.")

    channels: dict[str, dict[str, str]] = {}
    for logical_channel, raw_modes in channels_raw.items():
        channel_name = _expect_non_empty_string(logical_channel, f"{location}.channels key")
        modes_raw = _expect_mapping(raw_modes, f"{location}.channels.{channel_name}")
        if not modes_raw:
            raise MachineProfileError(f"{location}.channels.{channel_name} must not be empty.")
        channel_modes: dict[str, str] = {}
        for raw_backend_name, pv_name in modes_raw.items():
            backend_name = normalize_mode(
                raw_backend_name,
                f"{location}.channels.{channel_name} backend",
            )
            if backend_name in channel_modes:
                raise MachineProfileError(
                    f"{location}.channels.{channel_name} declares duplicate backend {backend_name!r}."
                )
            channel_modes[backend_name] = _expect_non_empty_string(
                pv_name,
                f"{location}.channels.{channel_name}.{backend_name}",
            )
        channels[channel_name] = channel_modes

    tags_raw = element_raw.get("tags", [])
    if not isinstance(tags_raw, list):
        raise MachineProfileError(f"{location}.tags must be a list.")
    tags = tuple(str(tag) for tag in tags_raw)
    plane = _parse_element_plane(element_raw.get("plane"), element_id, kind, tags, location)
    roles = _parse_element_roles(element_raw.get("roles"), kind, tags, plane, location)

    limits_raw = element_raw.get("limits", {})
    if not isinstance(limits_raw, Mapping):
        raise MachineProfileError(f"{location}.limits must be a mapping.")

    order = element_raw.get("order")
    if not isinstance(order, int):
        raise MachineProfileError(f"{location}.order must be an integer.")

    return ElementConfig(
        id=element_id,
        kind=kind,
        display_name=_expect_non_empty_string(
            element_raw.get("display_name"),
            f"{location}.display_name",
        ),
        order=order,
        plane=plane,
        roles=roles,
        tags=tags,
        limits=dict(limits_raw),
        channels=channels,
    )


def _parse_element_plane(
    raw_plane: Any,
    element_id: str,
    kind: str,
    tags: tuple[str, ...],
    location: str,
) -> str | None:
    if raw_plane is not None:
        return normalize_plane(raw_plane, f"{location}.plane")
    return _infer_element_plane(element_id, kind, tags)


def _parse_element_roles(
    raw_roles: Any,
    kind: str,
    tags: tuple[str, ...],
    plane: str | None,
    location: str,
) -> tuple[str, ...]:
    roles = list(_infer_element_roles(kind, tags, plane))
    if raw_roles is not None:
        roles.extend(_expect_optional_string_list(raw_roles, f"{location}.roles"))
    return tuple(dict.fromkeys(roles))


def _infer_element_plane(
    element_id: str,
    kind: str,
    tags: tuple[str, ...],
) -> str | None:
    if kind != "corr":
        return None

    normalized_id = element_id.upper()
    normalized_tags = {tag.lower() for tag in tags}

    if normalized_id.startswith("XC") or normalized_id.startswith("HC") or normalized_id.endswith(":HC"):
        return "x"
    if normalized_id.startswith("YC") or normalized_id.startswith("VC") or normalized_id.endswith(":VC"):
        return "y"
    if "x" in normalized_tags or "horizontal" in normalized_tags:
        return "x"
    if "y" in normalized_tags or "vertical" in normalized_tags:
        return "y"
    return None


def _infer_element_roles(
    kind: str,
    tags: tuple[str, ...],
    plane: str | None,
) -> tuple[str, ...]:
    normalized_tags = {tag.lower().strip() for tag in tags}
    roles: list[str] = []

    if kind == "bpm":
        if "orbit" in normalized_tags:
            roles.append("orbit_bpm")
        if "bba" in normalized_tags:
            roles.append("bba_bpm")
    elif kind == "corr":
        if "bba" in normalized_tags:
            roles.append("bba_corr")
        if "orbit" in normalized_tags:
            if plane == "x":
                roles.append("orbit_hcorr")
            elif plane == "y":
                roles.append("orbit_vcorr")
    elif kind == "flag":
        if "emit" in normalized_tags or "emit_measure" in normalized_tags:
            roles.append("emit_flag")

    return tuple(roles)


def _validate_orbit_workflow(
    raw_workflow: Any,
    elements_by_id: Mapping[str, ElementConfig],
) -> None:
    if raw_workflow is None:
        return
    workflow = _expect_mapping(raw_workflow, "workflows.orbit")
    if not any(name in workflow for name in ("bpms", "xcors", "ycors")):
        return
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
    default_target_bpms = _expect_optional_string_list(
        workflow.get("default_target_bpms"),
        "workflows.orbit.default_target_bpms",
    )
    _validate_element_refs(
        default_target_bpms,
        elements_by_id,
        "workflows.orbit.default_target_bpms",
        expected_kind="bpm",
    )
    unknown_defaults = [bpm for bpm in default_target_bpms if bpm not in bpms]
    if unknown_defaults:
        raise MachineProfileError(
            "workflows.orbit.default_target_bpms must be a subset of workflows.orbit.bpms; "
            f"unknown default target(s): {', '.join(unknown_defaults)}."
        )


def _validate_bba_workflow(
    raw_workflow: Any,
    elements_by_id: Mapping[str, ElementConfig],
) -> None:
    if raw_workflow is None:
        return
    workflow = _expect_mapping(raw_workflow, "workflows.bba")
    presets = _expect_list(workflow.get("presets"), "workflows.bba.presets")
    _validate_unique_ids(presets, "workflows.bba.presets")
    for index, raw_preset in enumerate(presets):
        location = f"workflows.bba.presets[{index}]"
        preset = _expect_mapping(raw_preset, location)
        _expect_non_empty_string(preset.get("id"), f"{location}.id")
        normalize_plane(preset.get("plane"), f"{location}.plane")
        _validate_element_ref(preset.get("quad"), elements_by_id, f"{location}.quad", expected_kind="quad")
        _validate_element_ref(preset.get("corr"), elements_by_id, f"{location}.corr", expected_kind="corr")
        _validate_element_ref(preset.get("bpm1"), elements_by_id, f"{location}.bpm1", expected_kind="bpm")
        _validate_element_ref(preset.get("bpm2"), elements_by_id, f"{location}.bpm2", expected_kind="bpm")
        if "mode" in preset:
            normalize_mode(preset.get("mode"), f"{location}.mode")
        analysis = _expect_optional_mapping(preset.get("analysis"), f"{location}.analysis")
        quad_leff = analysis.get("quad_leff")
        if quad_leff is not None:
            try:
                quad_leff_value = float(quad_leff)
            except (TypeError, ValueError) as exc:
                raise MachineProfileError(f"{location}.analysis.quad_leff must be numeric.") from exc
            if quad_leff_value <= 0:
                raise MachineProfileError(f"{location}.analysis.quad_leff must be positive.")

    bba1 = _expect_optional_mapping(workflow.get("bba1"), "workflows.bba.bba1")
    _validate_bba_family(bba1, elements_by_id, "workflows.bba.bba1")
    _validate_family_default_preset(
        bba1,
        "bba1",
        presets,
        "workflows.bba.bba1.default_preset",
    )

    bba2 = _expect_optional_mapping(workflow.get("bba2"), "workflows.bba.bba2")
    _validate_bba_family(bba2, elements_by_id, "workflows.bba.bba2")
    control_backends = _expect_bba_control_backends(bba2, "workflows.bba.bba2")
    for index, backend in enumerate(control_backends):
        normalize_mode(backend, f"workflows.bba.bba2.control_backends[{index}]")
    _validate_family_default_preset(
        bba2,
        "bba2",
        presets,
        "workflows.bba.bba2.default_preset",
    )


def _validate_bba_family(
    raw_family: Mapping[str, Any],
    elements_by_id: Mapping[str, ElementConfig],
    location: str,
) -> None:
    _validate_optional_element_refs(
        raw_family.get("correctors"),
        elements_by_id,
        f"{location}.correctors",
        expected_kind="corr",
    )
    _validate_optional_element_refs(
        raw_family.get("quads"),
        elements_by_id,
        f"{location}.quads",
        expected_kind="quad",
    )
    _validate_optional_element_refs(
        raw_family.get("bpm1"),
        elements_by_id,
        f"{location}.bpm1",
        expected_kind="bpm",
    )
    _validate_optional_element_refs(
        raw_family.get("bpm2"),
        elements_by_id,
        f"{location}.bpm2",
        expected_kind="bpm",
    )


def _expect_bba_control_backends(raw_family: Mapping[str, Any], location: str) -> list[str]:
    if "control_backends" in raw_family:
        return _expect_optional_string_list(
            raw_family.get("control_backends"),
            f"{location}.control_backends",
        )
    if "modes" in raw_family:
        return _expect_optional_string_list(raw_family.get("modes"), f"{location}.modes")
    return []


def _validate_emit_measure_workflow(
    raw_workflow: Any,
    elements_by_id: Mapping[str, ElementConfig],
) -> None:
    if raw_workflow is None:
        return
    workflow = _expect_mapping(raw_workflow, "workflows.emit_measure")
    presets = _expect_list(workflow.get("presets"), "workflows.emit_measure.presets")
    _validate_unique_ids(presets, "workflows.emit_measure.presets")
    for index, raw_preset in enumerate(presets):
        location = f"workflows.emit_measure.presets[{index}]"
        preset = _expect_mapping(raw_preset, location)
        _expect_non_empty_string(preset.get("id"), f"{location}.id")
        _validate_element_ref(preset.get("quad"), elements_by_id, f"{location}.quad", expected_kind="quad")
        _validate_element_ref(preset.get("flag"), elements_by_id, f"{location}.flag", expected_kind="flag")
        if "model_line" in preset:
            _expect_non_empty_string(preset.get("model_line"), f"{location}.model_line")
        scan = _expect_mapping(preset.get("scan", {}), f"{location}.scan")
        if "quadrupole" in scan or "sampling" in scan:
            quadrupole = _expect_mapping(
                scan.get("quadrupole"), f"{location}.scan.quadrupole"
            )
            sampling = _expect_mapping(
                scan.get("sampling"), f"{location}.scan.sampling"
            )
            scan_range = {
                "k1_from": quadrupole.get("low"),
                "k1_end": quadrupole.get("high"),
            }
            for field_name in ("low", "high", "steps", "unit", "mode"):
                if field_name not in quadrupole:
                    raise MachineProfileError(
                        f"{location}.scan.quadrupole is missing {field_name}."
                    )
            for field_name in (
                "samples_per_point", "settle_time_s", "sample_interval_s"
            ):
                if field_name not in sampling:
                    raise MachineProfileError(
                        f"{location}.scan.sampling is missing {field_name}."
                    )
        else:
            scan_range = scan
        adaptive = scan.get("adaptive")
        if adaptive is not None:
            adaptive_location = f"{location}.scan.adaptive"
            adaptive = _expect_mapping(adaptive, adaptive_location)
            structured_adaptive = "low" in adaptive or "high" in adaptive
            low_key = "low" if structured_adaptive else "k1_min"
            high_key = "high" if structured_adaptive else "k1_max"
            required_adaptive = (
                low_key,
                high_key,
                "initial_points",
                "target_points_per_plane",
                "max_unique_points",
            )
            missing = [name for name in required_adaptive if name not in adaptive]
            if missing:
                raise MachineProfileError(
                    f"{adaptive_location} is missing required field(s): {', '.join(missing)}."
                )
            k1_min = adaptive[low_key]
            k1_max = adaptive[high_key]
            if not isinstance(k1_min, (int, float)) or not isinstance(k1_max, (int, float)):
                raise MachineProfileError(f"{adaptive_location} K1 bounds must be numeric.")
            if k1_min >= k1_max:
                raise MachineProfileError(f"{adaptive_location}.k1_min must be below k1_max.")
            initial_points = adaptive["initial_points"]
            target_points = adaptive["target_points_per_plane"]
            max_points = adaptive["max_unique_points"]
            if not isinstance(initial_points, int) or initial_points < 3:
                raise MachineProfileError(f"{adaptive_location}.initial_points must be >= 3.")
            if not isinstance(target_points, int) or target_points < 3:
                raise MachineProfileError(
                    f"{adaptive_location}.target_points_per_plane must be >= 3."
                )
            if not isinstance(max_points, int) or max_points < initial_points:
                raise MachineProfileError(
                    f"{adaptive_location}.max_unique_points must be >= initial_points."
                )
            ratio = adaptive.get("waist_size_squared_ratio", 2.0)
            tolerance = adaptive.get("reuse_tolerance", 0.01)
            retries = adaptive.get("max_retries", 2)
            if not isinstance(ratio, (int, float)) or ratio <= 1:
                raise MachineProfileError(
                    f"{adaptive_location}.waist_size_squared_ratio must be > 1."
                )
            if not isinstance(tolerance, (int, float)) or tolerance < 0:
                raise MachineProfileError(
                    f"{adaptive_location}.reuse_tolerance must be non-negative."
                )
            if not isinstance(retries, int) or retries < 0:
                raise MachineProfileError(
                    f"{adaptive_location}.max_retries must be a non-negative integer."
                )
            for endpoint in ("k1_from", "k1_end"):
                value = scan_range.get(endpoint)
                if value is not None and not k1_min <= value <= k1_max:
                    raise MachineProfileError(
                        f"{location}.scan.{endpoint} must be inside adaptive K1 bounds."
                    )
        analysis = _expect_mapping(preset.get("analysis", {}), f"{location}.analysis")
        energy = analysis.get("energy_mev", preset.get("energy_mev"))
        if not isinstance(energy, (int, float)) or energy <= 0:
            raise MachineProfileError(f"{location}.energy_mev must be a positive number.")

    if "default_preset" in workflow:
        _validate_preset_ref(
            workflow.get("default_preset"),
            presets,
            "workflows.emit_measure.default_preset",
        )
    elif not presets:
        raise MachineProfileError("workflows.emit_measure.presets must not be empty.")
    _validate_optional_element_refs(
        workflow.get("twiss_quads"),
        elements_by_id,
        "workflows.emit_measure.twiss_quads",
        expected_kind="quad",
    )


def _validate_solenoid_centering_workflow(
    raw_workflow: Any,
    elements_by_id: Mapping[str, ElementConfig],
) -> None:
    if raw_workflow is None:
        return
    workflow = _expect_mapping(raw_workflow, "workflows.solenoid_centering")
    presets = _expect_list(workflow.get("presets"), "workflows.solenoid_centering.presets")
    if not presets:
        raise MachineProfileError("workflows.solenoid_centering.presets must not be empty.")
    _validate_unique_ids(presets, "workflows.solenoid_centering.presets")
    for index, raw_preset in enumerate(presets):
        location = f"workflows.solenoid_centering.presets[{index}]"
        preset = _expect_mapping(raw_preset, location)
        _expect_non_empty_string(preset.get("id"), f"{location}.id")
        _expect_non_empty_string(preset.get("display_name"), f"{location}.display_name")
        if preset.get("solenoid") is not None:
            _validate_element_ref(
                preset.get("solenoid"),
                elements_by_id,
                f"{location}.solenoid",
                expected_kind="solenoid",
            )
        elif preset.get("solenoid_setpoint_pv") is not None:
            _expect_non_empty_string(
                preset.get("solenoid_setpoint_pv"),
                f"{location}.solenoid_setpoint_pv",
            )
        else:
            raise MachineProfileError(
                f"{location} must define solenoid or solenoid_setpoint_pv."
            )
        if preset.get("solenoid_readback_pv") is not None:
            _expect_non_empty_string(
                preset.get("solenoid_readback_pv"),
                f"{location}.solenoid_readback_pv",
            )
        _validate_element_ref(preset.get("hcorr"), elements_by_id, f"{location}.hcorr", expected_kind="corr")
        _validate_element_ref(preset.get("vcorr"), elements_by_id, f"{location}.vcorr", expected_kind="corr")
        _validate_element_ref(preset.get("bpm"), elements_by_id, f"{location}.bpm", expected_kind="bpm")
        _validate_scan_range(preset.get("solenoid_scan"), f"{location}.solenoid_scan")
        _validate_scan_range(preset.get("corrector_scan"), f"{location}.corrector_scan")
        _validate_positive_int(preset.get("samples_per_point"), f"{location}.samples_per_point")
        _validate_nonnegative_float(preset.get("settle_time_s"), f"{location}.settle_time_s")
        _validate_nonnegative_float(preset.get("sample_interval_s"), f"{location}.sample_interval_s")
        max_iters = preset.get("max_iters")
        legacy_max_rounds = preset.get("max_rounds")
        if max_iters is not None and legacy_max_rounds is not None:
            raise MachineProfileError(
                f"{location} must not define both max_iters and max_rounds."
            )
        _validate_positive_int(
            max_iters if max_iters is not None else legacy_max_rounds,
            f"{location}.max_iters",
        )

    if "default_preset" in workflow:
        _validate_preset_ref(
            workflow.get("default_preset"),
            presets,
            "workflows.solenoid_centering.default_preset",
        )


def _validate_scan_range(raw_scan: Any, location: str) -> None:
    scan = _expect_mapping(raw_scan, location)
    relative_from = _expect_float(scan.get("relative_from"), f"{location}.relative_from")
    relative_to = _expect_float(scan.get("relative_to"), f"{location}.relative_to")
    if relative_from == relative_to:
        raise MachineProfileError(f"{location}.relative_from and relative_to must differ.")
    _validate_positive_int(scan.get("steps"), f"{location}.steps")


def _validate_positive_int(value: Any, location: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise MachineProfileError(f"{location} must be a positive integer.")
    return value


def _validate_nonnegative_float(value: Any, location: str) -> float:
    selected = _expect_float(value, location)
    if selected < 0:
        raise MachineProfileError(f"{location} must be >= 0.")
    return selected


def _expect_float(value: Any, location: str) -> float:
    if not isinstance(value, (int, float)):
        raise MachineProfileError(f"{location} must be numeric.")
    return float(value)


def _validate_unique_ids(items: list[Any], location: str) -> None:
    seen: set[str] = set()
    for index, raw_item in enumerate(items):
        item = _expect_mapping(raw_item, f"{location}[{index}]")
        item_id = _expect_non_empty_string(item.get("id"), f"{location}[{index}].id")
        if item_id in seen:
            raise MachineProfileError(f"Duplicate id {item_id!r} in {location}.")
        seen.add(item_id)


def _parse_profile_control_backends(
    raw_backends: Any,
    elements: list[ElementConfig],
) -> tuple[str, ...]:
    if raw_backends is not None:
        return tuple(
            _dedupe_in_order(
                normalize_mode(backend, f"control_backends[{index}]")
                for index, backend in enumerate(
                    _expect_string_list(raw_backends, "control_backends")
                )
            )
        )

    discovered: list[str] = []
    for element in elements:
        for channel_modes in element.channels.values():
            discovered.extend(channel_modes.keys())
    return tuple(_dedupe_in_order(discovered))


def _validate_element_backends(
    elements: list[ElementConfig],
    control_backends: tuple[str, ...],
) -> None:
    allowed = set(control_backends)
    for element in elements:
        for logical_channel, channel_modes in element.channels.items():
            unknown = set(channel_modes) - allowed
            if unknown:
                backend_list = ", ".join(sorted(unknown))
                raise MachineProfileError(
                    f"Element {element.id} channel {logical_channel!r} declares unknown control backend(s): "
                    f"{backend_list}."
                )


def _parse_machine_runtime(raw_runtime: Any, location: str) -> MachineRuntimeConfig:
    runtime = _expect_mapping(raw_runtime, location)
    vm = _expect_mapping(runtime.get("vm"), f"{location}.vm")
    softioc = _expect_mapping(runtime.get("softioc"), f"{location}.softioc")
    return MachineRuntimeConfig(
        vm=MachineVmRuntimeConfig(
            root=_expect_non_empty_string(vm.get("root"), f"{location}.vm.root"),
            ui_entrypoint=_expect_non_empty_string(
                vm.get("ui_entrypoint"),
                f"{location}.vm.ui_entrypoint",
            ),
            manager_entrypoint=_expect_non_empty_string(
                vm.get("manager_entrypoint"),
                f"{location}.vm.manager_entrypoint",
            ),
            runtime_json=_expect_non_empty_string(
                vm.get("runtime_json"),
                f"{location}.vm.runtime_json",
            ),
            bootstrap_lattice=_expect_non_empty_string(
                vm.get("bootstrap_lattice"),
                f"{location}.vm.bootstrap_lattice",
            ),
            bootstrap_ele=_expect_non_empty_string(
                vm.get("bootstrap_ele"),
                f"{location}.vm.bootstrap_ele",
            ),
            line_name=_expect_non_empty_string(
                vm.get("line_name"),
                f"{location}.vm.line_name",
            ),
        ),
        softioc=MachineSoftIocRuntimeConfig(
            root=_expect_non_empty_string(
                softioc.get("root"),
                f"{location}.softioc.root",
            ),
            substitutions_file=_expect_non_empty_string(
                softioc.get("substitutions_file"),
                f"{location}.softioc.substitutions_file",
            ),
        ),
    )


def _dedupe_in_order(items: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _order_control_backends(
    control_backends: tuple[str, ...],
    default_mode: str,
) -> tuple[str, ...]:
    ordered = list(control_backends)
    if default_mode in ordered:
        ordered.remove(default_mode)
        ordered.insert(0, default_mode)
    return tuple(ordered)


def _validate_preset_ref(preset_id: Any, presets: list[Any], location: str) -> None:
    required_id = _expect_non_empty_string(preset_id, location)
    preset_ids = {
        _expect_non_empty_string(_expect_mapping(item, location).get("id"), f"{location}.id")
        for item in presets
    }
    if required_id not in preset_ids:
        raise MachineProfileError(f"{location} references unknown preset {required_id!r}.")


def _validate_family_default_preset(
    raw_family: Mapping[str, Any],
    family_name: str,
    presets: list[Any],
    location: str,
) -> None:
    if "default_preset" in raw_family:
        _validate_preset_ref(raw_family.get("default_preset"), presets, location)
        return

    matching_ids: list[str] = []
    for index, raw_item in enumerate(presets):
        item = _expect_mapping(raw_item, f"{location}.presets[{index}]")
        if _expect_non_empty_string(item.get("family"), f"{location}.presets[{index}].family") == family_name:
            matching_ids.append(
                _expect_non_empty_string(item.get("id"), f"{location}.presets[{index}].id")
            )
    if not matching_ids:
        raise MachineProfileError(
            f"{location} is missing default_preset and no preset with family {family_name!r} was found."
        )


def _validate_element_refs(
    refs: list[str],
    elements_by_id: Mapping[str, ElementConfig],
    location: str,
    expected_kind: str | None = None,
) -> None:
    for ref in refs:
        _validate_element_ref(ref, elements_by_id, location, expected_kind=expected_kind)


def _validate_optional_element_refs(
    raw_refs: Any,
    elements_by_id: Mapping[str, ElementConfig],
    location: str,
    expected_kind: str | None = None,
) -> None:
    if raw_refs is None:
        return
    refs = _expect_optional_string_list(raw_refs, location)
    _validate_element_refs(refs, elements_by_id, location, expected_kind=expected_kind)


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


def _expect_optional_mapping(value: Any, location: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _expect_mapping(value, location)


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


def _expect_optional_string_list(value: Any, location: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MachineProfileError(f"{location} must be a list of strings.")
    return [_expect_non_empty_string(item, f"{location}[{index}]") for index, item in enumerate(value)]


def _expect_non_empty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MachineProfileError(f"{location} must be a non-empty string.")
    return value.strip()
