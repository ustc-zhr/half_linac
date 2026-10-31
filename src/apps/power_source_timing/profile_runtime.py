from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from half_linac.src.shared.machine_profile import (
    AppContext,
    FlagPixelGeometry,
    MachineProfileError,
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_element_image_geometry,
    resolve_write_target,
)

from .model import DEVICES, WAVEFORM_DEVICES


FIELD_SUFFIXES = (
    "delay_set",
    "delay_readback",
    "enable",
    "width_set",
    "width_readback",
)


@dataclass(frozen=True)
class TimingGroup:
    element_id: str
    display_name: str
    channels: Mapping[str, Mapping[str, str]]
    waveforms: Mapping[str, str]

    @property
    def devices(self) -> tuple[str, ...]:
        return tuple(device for device in DEVICES if device in self.channels)

    def pv(self, device: str, field: str) -> str:
        try:
            return self.channels[device][field]
        except KeyError as exc:
            raise MachineProfileError(
                f"{self.element_id} has no timing PV for {device}.{field}."
            ) from exc


@dataclass(frozen=True)
class WaveformAlignmentConfig:
    sample_rate_mhz: float | None
    shared_time_origin: bool
    reference_device: str
    default_display_mode: str
    default_threshold_fraction: float
    baseline_fraction: float
    refresh_interval_ms: int
    stale_after_s: float


@dataclass(frozen=True)
class TimingEnergyScanConfig:
    enabled: bool
    required_devices: tuple[str, ...]
    offset_low_us: float
    offset_high_us: float
    offset_step_us: float
    hard_limit_us: float
    timing_settle_time_s: float
    readback_timeout_s: float
    readback_poll_interval_s: float
    tracking_half_window_mev: float
    samples_per_point: int
    sample_interval_s: float
    min_valid_samples: int
    min_relative_brightness: float
    energy_element: str
    energy_channel: str
    energy_pv: str
    energy_low_mev: float
    energy_high_mev: float
    flag_element: str
    flag_image_channel: str
    flag_image_pv: str
    flag_exposure_pv: str | None
    image_geometry: FlagPixelGeometry
    x_reference_mm: float
    design_eta_m: float
    energy_match: Mapping[str, Any]
    energy_match_defaults: Mapping[str, Any]


@dataclass(frozen=True)
class TimingRuntime:
    context: AppContext
    groups: tuple[TimingGroup, ...]
    default_element: str
    minimum_us: float
    readback_tolerance_us: float
    delay_step_us: float
    width_step_us: float
    step_choices_us: tuple[float, ...]
    button_repeat_delay_ms: int
    button_repeat_interval_ms: int
    waveform_alignment: WaveformAlignmentConfig
    linked_energy_scan: "TimingEnergyScanConfig | None" = None


def load_timing_runtime() -> TimingRuntime:
    context = load_app_context("power_source_timing")
    backend = context.control_backend.name
    require_workflow_write_allowed(
        context,
        "power_source_timing",
        "Power-source timing write",
    )
    raw = context.profile.workflows.get("power_source_timing")
    if not isinstance(raw, Mapping):
        raise MachineProfileError("Missing power_source_timing workflow configuration.")
    tag = str(raw.get("element_tag", "")).strip()
    candidates = [element for element in context.profile.elements if tag in element.tags]
    groups: list[TimingGroup] = []
    for element in candidates:
        channels: dict[str, dict[str, str]] = {}
        for device in DEVICES:
            logical_channels = [f"{device}_{suffix}" for suffix in FIELD_SUFFIXES]
            if not any(logical in element.channels for logical in logical_channels):
                continue
            device_channels: dict[str, str] = {}
            for suffix in FIELD_SUFFIXES:
                logical = f"{device}_{suffix}"
                try:
                    device_channels[suffix] = element.channels[logical][backend]
                except KeyError as exc:
                    raise MachineProfileError(
                        f"{element.id} is missing {backend!r} channel {logical!r}."
                    ) from exc
            channels[device] = device_channels
        if not channels:
            raise MachineProfileError(
                f"{element.id} has no complete power-source timing device channels."
            )
        waveforms = {
            device: element.channels[f"{device}_waveform"][backend]
            for device in WAVEFORM_DEVICES
            if f"{device}_waveform" in element.channels
            and backend in element.channels[f"{device}_waveform"]
        }
        groups.append(
            TimingGroup(element.id, element.display_name, channels, waveforms)
        )
    if not groups:
        raise MachineProfileError("No power-source timing groups are configured.")

    step_choices = _positive_numbers(raw.get("step_choices_us"), "step_choices_us")
    alignment_raw = raw.get("waveform_alignment", {})
    if not isinstance(alignment_raw, Mapping):
        raise MachineProfileError(
            "power_source_timing.waveform_alignment must be a mapping."
        )
    reference_device = str(
        alignment_raw.get("reference_device", "llrf")
    ).strip().lower()
    if reference_device not in WAVEFORM_DEVICES:
        raise MachineProfileError(
            "power_source_timing.waveform reference_device must be a supported "
            "waveform channel."
        )
    display_mode = str(
        alignment_raw.get("default_display_mode", "normalized")
    ).strip().lower()
    if display_mode not in {"raw", "normalized"}:
        raise MachineProfileError(
            "power_source_timing waveform display mode must be raw or normalized."
        )
    alignment = WaveformAlignmentConfig(
        sample_rate_mhz=(
            _positive(
                alignment_raw.get("sample_rate_mhz"),
                "waveform_alignment.sample_rate_mhz",
            )
            if alignment_raw.get("sample_rate_mhz") is not None
            else None
        ),
        shared_time_origin=_boolean(
            alignment_raw.get("shared_time_origin", False),
            "waveform_alignment.shared_time_origin",
        ),
        reference_device=reference_device,
        default_display_mode=display_mode,
        default_threshold_fraction=_fraction(
            alignment_raw.get("default_threshold_fraction", 0.5),
            "waveform_alignment.default_threshold_fraction",
        ),
        baseline_fraction=_fraction(
            alignment_raw.get("baseline_fraction", 0.1),
            "waveform_alignment.baseline_fraction",
        ),
        refresh_interval_ms=_positive_int(
            alignment_raw.get("refresh_interval_ms", 200),
            "waveform_alignment.refresh_interval_ms",
        ),
        stale_after_s=_positive(
            alignment_raw.get("stale_after_s", 2.0),
            "waveform_alignment.stale_after_s",
        ),
    )
    linked_energy_scan = _parse_linked_energy_scan(context, raw)
    return TimingRuntime(
        context=context,
        groups=tuple(groups),
        default_element=str(raw.get("default_element", "")),
        minimum_us=_nonnegative(raw.get("minimum_us"), "minimum_us"),
        readback_tolerance_us=_positive(
            raw.get("readback_tolerance_us"), "readback_tolerance_us"
        ),
        delay_step_us=_positive(raw.get("delay_step_us"), "delay_step_us"),
        width_step_us=_positive(raw.get("width_step_us"), "width_step_us"),
        step_choices_us=step_choices,
        button_repeat_delay_ms=_positive_int(
            raw.get("button_repeat_delay_ms"), "button_repeat_delay_ms"
        ),
        button_repeat_interval_ms=_positive_int(
            raw.get("button_repeat_interval_ms"), "button_repeat_interval_ms"
        ),
        waveform_alignment=alignment,
        linked_energy_scan=linked_energy_scan,
    )



def _parse_linked_energy_scan(
    context: AppContext,
    workflow: Mapping[str, Any],
) -> TimingEnergyScanConfig | None:
    raw = workflow.get("linked_energy_scan")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise MachineProfileError(
            "power_source_timing.linked_energy_scan must be a mapping."
        )
    enabled = _boolean(
        raw.get("enabled", True),
        "power_source_timing.linked_energy_scan.enabled",
    )
    required_raw = raw.get("required_devices", list(DEVICES))
    if not isinstance(required_raw, list):
        raise MachineProfileError(
            "power_source_timing.linked_energy_scan.required_devices must be a list."
        )
    required_devices = tuple(str(value).strip().lower() for value in required_raw)
    if required_devices != DEVICES:
        raise MachineProfileError(
            "power_source_timing linked scan requires hv, llrf, ssa, and kly."
        )

    offset = raw.get("offset")
    if not isinstance(offset, Mapping):
        raise MachineProfileError(
            "power_source_timing.linked_energy_scan.offset must be a mapping."
        )
    low = _number(offset.get("low"), "linked_energy_scan.offset.low")
    high = _number(offset.get("high"), "linked_energy_scan.offset.high")
    step = _positive(offset.get("step"), "linked_energy_scan.offset.step")
    hard_limit = _positive(
        offset.get("hard_limit"), "linked_energy_scan.offset.hard_limit"
    )
    if str(offset.get("unit", "")).strip().lower() not in {"us", "μs"}:
        raise MachineProfileError(
            "power_source_timing.linked_energy_scan.offset.unit must be 'us'."
        )
    if not (-hard_limit <= low <= 0 <= high <= hard_limit) or low >= high:
        raise MachineProfileError(
            "power_source_timing linked scan range must contain zero and remain "
            "inside the configured hard limit."
        )
    for endpoint in (abs(low), abs(high)):
        if not math.isclose(endpoint / step, round(endpoint / step), abs_tol=1e-9):
            raise MachineProfileError(
                "power_source_timing linked scan endpoints must lie on the step grid."
            )

    timing = raw.get("timing")
    if not isinstance(timing, Mapping):
        raise MachineProfileError(
            "power_source_timing.linked_energy_scan.timing must be a mapping."
        )
    verification = timing.get("readback_verification", {})
    if not isinstance(verification, Mapping):
        raise MachineProfileError(
            "power_source_timing linked scan readback_verification must be a mapping."
        )
    timing_settle = _nonnegative(
        timing.get("settle_time_s"), "linked_energy_scan.timing.settle_time_s"
    )
    readback_timeout = _positive(
        verification.get("timeout_s"),
        "linked_energy_scan.timing.readback_verification.timeout_s",
    )
    readback_poll = _positive(
        verification.get("poll_interval_s"),
        "linked_energy_scan.timing.readback_verification.poll_interval_s",
    )

    scan = raw.get("scan")
    if not isinstance(scan, Mapping):
        raise MachineProfileError(
            "power_source_timing.linked_energy_scan.scan must be a mapping."
        )
    tracking = scan.get("energy_tracking")
    sampling = scan.get("point_measurement")
    if not isinstance(tracking, Mapping) or not isinstance(sampling, Mapping):
        raise MachineProfileError(
            "power_source_timing linked scan requires energy_tracking and "
            "point_measurement mappings."
        )
    tracking_half_window = _positive(
        tracking.get("tracking_half_window_mev"),
        "linked_energy_scan.scan.energy_tracking.tracking_half_window_mev",
    )
    samples = _positive_int(
        sampling.get("samples_per_point"),
        "linked_energy_scan.scan.point_measurement.samples_per_point",
    )
    min_valid = _positive_int(
        sampling.get("min_valid_samples"),
        "linked_energy_scan.scan.point_measurement.min_valid_samples",
    )
    if min_valid > samples:
        raise MachineProfileError(
            "power_source_timing linked scan min_valid_samples exceeds samples_per_point."
        )
    sample_interval = _nonnegative(
        sampling.get("sample_interval_s"),
        "linked_energy_scan.scan.point_measurement.sample_interval_s",
    )
    min_relative_brightness = _number(
        sampling.get("fit_min_relative_brightness", 0.05),
        "linked_energy_scan.scan.point_measurement.fit_min_relative_brightness",
    )
    if not 0 <= min_relative_brightness <= 1:
        raise MachineProfileError(
            "power_source_timing linked scan relative brightness must be in [0, 1]."
        )

    diagnostics = raw.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        raise MachineProfileError(
            "power_source_timing.linked_energy_scan.diagnostics must be a mapping."
        )
    flag_element = str(diagnostics.get("flag_element", "")).strip()
    flag_channel = str(diagnostics.get("flag_image_channel", "")).strip()
    energy_element = str(raw.get("energy_element", "")).strip()
    energy_channel = str(raw.get("energy_set_channel", "")).strip()
    if not all((flag_element, flag_channel, energy_element, energy_channel)):
        raise MachineProfileError(
            "power_source_timing linked scan diagnostic and energy channels are required."
        )
    geometry = resolve_element_image_geometry(
        context, flag_element, context.control_backend.name
    )
    flag_image_pv = resolve_channel(context, flag_element, flag_channel)
    try:
        flag_exposure_pv = resolve_channel(context, flag_element, "exposure_time")
    except MachineProfileError:
        flag_exposure_pv = None
    energy_target = resolve_write_target(
        context,
        energy_element,
        logical_channel=energy_channel,
        unit="MeV",
    )
    if energy_target.machine_limit.low is None or energy_target.machine_limit.high is None:
        raise MachineProfileError(
            f"{energy_element}.{energy_channel} requires finite machine limits."
        )

    energy_match = raw.get("energy_match")
    defaults = raw.get("energy_match_defaults")
    if not isinstance(energy_match, Mapping) or not isinstance(defaults, Mapping):
        raise MachineProfileError(
            "power_source_timing linked scan requires Energy Match configuration."
        )
    search = energy_match.get("range")
    if not isinstance(search, Mapping):
        raise MachineProfileError(
            "power_source_timing linked scan Energy Match range is required."
        )
    energy_low = max(
        float(energy_target.machine_limit.low),
        _number(search.get("low"), "linked_energy_scan.energy_match.range.low"),
    )
    energy_high = min(
        float(energy_target.machine_limit.high),
        _number(search.get("high"), "linked_energy_scan.energy_match.range.high"),
    )
    if energy_low >= energy_high:
        raise MachineProfileError(
            "power_source_timing Energy Match range does not overlap machine limits."
        )

    return TimingEnergyScanConfig(
        enabled=enabled,
        required_devices=required_devices,
        offset_low_us=low,
        offset_high_us=high,
        offset_step_us=step,
        hard_limit_us=hard_limit,
        timing_settle_time_s=timing_settle,
        readback_timeout_s=readback_timeout,
        readback_poll_interval_s=readback_poll,
        tracking_half_window_mev=tracking_half_window,
        samples_per_point=samples,
        sample_interval_s=sample_interval,
        min_valid_samples=min_valid,
        min_relative_brightness=min_relative_brightness,
        energy_element=energy_element,
        energy_channel=energy_channel,
        energy_pv=energy_target.pv_name,
        energy_low_mev=energy_low,
        energy_high_mev=energy_high,
        flag_element=flag_element,
        flag_image_channel=flag_channel,
        flag_image_pv=flag_image_pv,
        flag_exposure_pv=flag_exposure_pv,
        image_geometry=geometry,
        x_reference_mm=_number(
            diagnostics.get("x_reference_mm"),
            "linked_energy_scan.diagnostics.x_reference_mm",
        ),
        design_eta_m=geometry.model_x_to_image_x(
            _number(
                diagnostics.get("design_eta_m"),
                "linked_energy_scan.diagnostics.design_eta_m",
            )
        ),
        energy_match=dict(energy_match),
        energy_match_defaults=dict(defaults),
    )

def _number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"power_source_timing.{name} must be numeric.") from exc
    if not math.isfinite(number):
        raise MachineProfileError(f"power_source_timing.{name} must be finite.")
    return number


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise MachineProfileError(f"{name} must be a boolean.")
    return value


def _positive(value: Any, name: str) -> float:
    number = _number(value, name)
    if number <= 0:
        raise MachineProfileError(f"power_source_timing.{name} must be positive.")
    return number


def _nonnegative(value: Any, name: str) -> float:
    number = _number(value, name)
    if number < 0:
        raise MachineProfileError(f"power_source_timing.{name} must not be negative.")
    return number


def _fraction(value: Any, name: str) -> float:
    number = _number(value, name)
    if not 0.0 < number < 1.0:
        raise MachineProfileError(
            f"power_source_timing.{name} must be between 0 and 1."
        )
    return number


def _positive_numbers(value: Any, name: str) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise MachineProfileError(f"power_source_timing.{name} must be a non-empty list.")
    numbers = tuple(_positive(item, f"{name}[]") for item in value)
    return tuple(dict.fromkeys(numbers))


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MachineProfileError(f"power_source_timing.{name} must be a positive integer.")
    return value
