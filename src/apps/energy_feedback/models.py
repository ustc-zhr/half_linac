"""Dependency-free runtime and control-domain types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class PVReading:
    key: str
    pv_name: str
    value: float | None
    connected: bool
    source_timestamp: float | None
    error: str = ""


@dataclass(frozen=True)
class MeasurementQuality:
    state: str
    reason: str
    connected: bool
    stale: bool = False

    @property
    def valid(self) -> bool:
        return self.state == "OK"


@dataclass(frozen=True)
class EnergyMeasurement:
    sampled_at: float
    source_timestamp: float | None
    target_source_timestamp: float | None
    energy_mev: float | None
    target_mev: float | None
    error_mev: float | None
    diagnostics: Mapping[str, float | None]
    diagnostic_errors: Mapping[str, str]
    quality: MeasurementQuality
    diagnostic_source_timestamps: Mapping[str, float | None] = field(
        default_factory=dict
    )
    marker_value: float | None = None
    marker_source_timestamp: float | None = None


@dataclass(frozen=True)
class ShadowActuator:
    id: str
    label: str
    coefficient: float
    unit: str
    max_step: float
    max_session_delta: float
    operating_range: tuple[float, float]
    machine_limit: tuple[float, float]
    setpoint_key: str = ""
    readback_key: str = ""
    verify_tolerance: float = 0.05
    command_precision: int = 2
    cyclic_period: float | None = None


@dataclass(frozen=True)
class ShadowControlGroup:
    id: str
    label: str
    response_mev_per_knob_unit: float
    loop_gain: float
    max_knob_step: float
    deadband_mev: float
    action_window_mev: float
    actuators: tuple[ShadowActuator, ...]


@dataclass(frozen=True)
class ActuatorCommand:
    actuator_id: str
    current: float
    delta: float
    proposed: float
    unit: str


@dataclass(frozen=True)
class FeedbackDecision:
    state: str
    reason: str
    error_mev: float | None
    knob_delta: float | None = None
    commands: tuple[ActuatorCommand, ...] = field(default_factory=tuple)
    write_verified: bool = False
    rollback_applied: bool = False
    write_detail: str = ""
    control_parameters: Mapping[str, float] = field(default_factory=dict)

    @property
    def has_valid_suggestion(self) -> bool:
        return self.state == "SHADOW" and bool(self.commands)
