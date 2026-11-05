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
    monitor_received_at: float | None = None


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
class FeedbackObservable:
    """Unit-safe scalar used by the feedback controller."""

    label: str
    unit: str
    value: float | None
    target: float | None = None
    error: float | None = None
    raw_value: float | None = None


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
    observable: FeedbackObservable | None = None

    @property
    def control_value(self) -> float | None:
        return self.observable.value if self.observable is not None else self.energy_mev

    @property
    def control_target(self) -> float | None:
        return self.observable.target if self.observable is not None else self.target_mev

    @property
    def control_error(self) -> float | None:
        return self.observable.error if self.observable is not None else self.error_mev

    @property
    def observable_label(self) -> str:
        return self.observable.label if self.observable is not None else "Energy"

    @property
    def observable_unit(self) -> str:
        return self.observable.unit if self.observable is not None else "MeV"


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
    command_basis: str = "readback"


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
