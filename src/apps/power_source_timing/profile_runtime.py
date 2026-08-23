from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    load_app_context,
    require_workflow_write_allowed,
)

from .model import DEVICES


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

    def pv(self, device: str, field: str) -> str:
        try:
            return self.channels[device][field]
        except KeyError as exc:
            raise MachineProfileError(
                f"{self.element_id} has no timing PV for {device}.{field}."
            ) from exc


@dataclass(frozen=True)
class WaveformAlignmentConfig:
    reference_device: str
    default_display_mode: str
    default_threshold_fraction: float
    baseline_fraction: float
    refresh_interval_ms: int
    stale_after_s: float


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
        waveforms = {
            device: element.channels[f"{device}_waveform"][backend]
            for device in DEVICES
            if f"{device}_waveform" in element.channels
            and backend in element.channels[f"{device}_waveform"]
        }
        groups.append(
            TimingGroup(element.id, element.display_name, channels, waveforms)
        )
    groups.sort(key=lambda group: group.element_id)
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
    if reference_device not in DEVICES:
        raise MachineProfileError(
            "power_source_timing.waveform reference_device must be a timing device."
        )
    display_mode = str(
        alignment_raw.get("default_display_mode", "normalized")
    ).strip().lower()
    if display_mode not in {"raw", "normalized"}:
        raise MachineProfileError(
            "power_source_timing waveform display mode must be raw or normalized."
        )
    alignment = WaveformAlignmentConfig(
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
    )


def _number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"power_source_timing.{name} must be numeric.") from exc
    if not math.isfinite(number):
        raise MachineProfileError(f"power_source_timing.{name} must be finite.")
    return number


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
