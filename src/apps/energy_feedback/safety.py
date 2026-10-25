"""Small explicit state machine for acquisition and feedback safety."""

from __future__ import annotations

from dataclasses import dataclass

from .models import EnergyMeasurement, FeedbackDecision


@dataclass(frozen=True)
class SafetySnapshot:
    state: str
    reason: str
    acquisition_active: bool
    feedback_requested: bool


class FeedbackSafetyStateMachine:
    """Track acquisition and operator feedback intent."""

    def __init__(self, *, has_control_group: bool, write_enabled: bool) -> None:
        self.has_control_group = bool(has_control_group)
        self.write_enabled = bool(write_enabled)
        self._acquisition_active = False
        self._feedback_requested = False
        self._state = "STOPPED"
        self._reason = "Acquisition stopped"

    @property
    def snapshot(self) -> SafetySnapshot:
        return SafetySnapshot(
            self._state,
            self._reason,
            self._acquisition_active,
            self._feedback_requested,
        )

    def start_acquisition(self) -> SafetySnapshot:
        self._acquisition_active = True
        self._feedback_requested = False
        self._state = "CONNECTING"
        self._reason = "Connection check in progress"
        return self.snapshot

    def stop(self) -> SafetySnapshot:
        self._acquisition_active = False
        self._feedback_requested = False
        self._state = "STOPPED"
        self._reason = "Acquisition stopped"
        return self.snapshot

    def fail(self, reason: str) -> SafetySnapshot:
        self._state = "HOLD"
        self._reason = str(reason)
        return self.snapshot

    def request_feedback(self, enabled: bool) -> SafetySnapshot:
        if enabled and not self.has_control_group:
            self._feedback_requested = False
            return self.fail("Feedback unavailable: actuator group not configured")
        if enabled and not self.write_enabled:
            self._feedback_requested = False
            return self.fail("Feedback writes are blocked by the machine profile")
        self._feedback_requested = bool(enabled)
        return self.snapshot

    def observe(
        self,
        measurement: EnergyMeasurement,
        decision: FeedbackDecision,
    ) -> SafetySnapshot:
        if not self._acquisition_active:
            return self.snapshot
        if not measurement.quality.valid:
            self._state = "HOLD"
            self._reason = measurement.quality.reason
        elif self._feedback_requested:
            self._state = decision.state
            self._reason = decision.reason
        else:
            self._state = "MONITOR"
            self._reason = "Monitoring"
        return self.snapshot
