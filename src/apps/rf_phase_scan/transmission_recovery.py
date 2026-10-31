from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from epics import caget, caput

from half_linac.src.apps.rf_phase_scan.phase_energy_scan import (
    phase_difference_deg,
    wait_for_phase_readback,
    wrap_phase_deg,
)
from half_linac.src.shared.machine_profile import (
    EnergyControlLock,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_write_target,
)


class TransmissionRecoveryCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class TransmissionRecoverySettings:
    half_range_deg: float
    points: int
    settle_time_s: float
    samples_per_point: int
    sample_interval_s: float
    min_valid_samples: int
    readback_channel: str = "phase_readback"
    readback_tolerance_deg: float = 0.1
    readback_timeout_s: float = 2.0
    readback_poll_interval_s: float = 0.05

    def validate(self) -> None:
        numeric = (
            self.half_range_deg,
            self.settle_time_s,
            self.sample_interval_s,
            self.readback_tolerance_deg,
            self.readback_timeout_s,
            self.readback_poll_interval_s,
        )
        if not all(math.isfinite(float(value)) for value in numeric):
            raise ValueError("Transmission Recovery settings must be finite.")
        if not 0 < self.half_range_deg <= 180:
            raise ValueError("Phase half range must be in (0, 180].")
        if self.points < 3 or self.points % 2 == 0:
            raise ValueError("Phase points must be an odd integer of at least 3.")
        if self.settle_time_s < 0 or self.sample_interval_s < 0:
            raise ValueError("Settle and sample intervals must not be negative.")
        if self.samples_per_point < 1:
            raise ValueError("Samples per point must be at least 1.")
        if not 1 <= self.min_valid_samples <= self.samples_per_point:
            raise ValueError("Minimum valid samples must be within samples per point.")
        if (
            self.readback_tolerance_deg < 0
            or self.readback_timeout_s < 0
            or self.readback_poll_interval_s <= 0
        ):
            raise ValueError("Phase readback verification settings are invalid.")

    def execution_offsets(self) -> tuple[float, ...]:
        self.validate()
        branch_points = (self.points - 1) // 2
        step = self.half_range_deg / branch_points
        positive = tuple(step * index for index in range(1, branch_points + 1))
        negative = tuple(-step * index for index in range(1, branch_points + 1))
        return (0.0, *positive, *negative)


@dataclass(frozen=True)
class TransmissionRecoveryStage:
    index: int
    rf_element: str
    target_bpm: str
    phase_pv: str
    phase_readback_pv: str
    signal_pv: str


@dataclass(frozen=True)
class TransmissionRecoveryCallbacks:
    item: Callable[[int, Mapping[str, Any]], None] = field(
        default=lambda _index, _record: None
    )
    point: Callable[[int, Mapping[str, Any]], None] = field(
        default=lambda _index, _point: None
    )
    state: Callable[[str], None] = field(default=lambda _state: None)


def settings_from_config(
    recovery_config: Mapping[str, Any],
) -> TransmissionRecoverySettings:
    phase = dict(recovery_config["phase_scan"])
    measurement = dict(recovery_config["measurement"])
    verification = dict(phase.get("readback_verification", {}))
    settings = TransmissionRecoverySettings(
        half_range_deg=float(phase["half_range_deg"]),
        points=int(phase["points"]),
        settle_time_s=float(phase["settle_time_s"]),
        samples_per_point=int(measurement["samples_per_point"]),
        sample_interval_s=float(measurement["sample_interval_s"]),
        min_valid_samples=int(measurement["min_valid_samples"]),
        readback_channel=str(verification.get("channel", "phase_readback")),
        readback_tolerance_deg=float(verification.get("tolerance_deg", 0.1)),
        readback_timeout_s=float(verification.get("timeout_s", 2.0)),
        readback_poll_interval_s=float(
            verification.get("poll_interval_s", 0.05)
        ),
    )
    settings.validate()
    return settings


def discover_recovery_stages(
    context,
    recovery_config: Mapping[str, Any],
    *,
    mode: str | None = None,
) -> tuple[TransmissionRecoveryStage, ...]:
    if not recovery_config.get("enabled", False):
        return ()
    measurement = dict(recovery_config["measurement"])
    signal_channel = str(measurement.get("channel", "sum"))
    verification = dict(recovery_config["phase_scan"].get("readback_verification", {}))
    readback_channel = str(verification.get("channel", "phase_readback"))
    stages = []
    for index, item in enumerate(recovery_config["stages"]):
        rf_element = str(item["rf_element"])
        target_bpm = str(item["target_bpm"])
        target = resolve_write_target(
            context,
            rf_element,
            logical_channel="phase_set",
            unit="deg",
            mode=mode,
        )
        stages.append(
            TransmissionRecoveryStage(
                index=index,
                rf_element=rf_element,
                target_bpm=target_bpm,
                phase_pv=target.pv_name,
                phase_readback_pv=resolve_channel(
                    context, rf_element, readback_channel, mode=mode
                ),
                signal_pv=resolve_channel(
                    context, target_bpm, signal_channel, mode=mode
                ),
            )
        )
    return tuple(stages)


class TransmissionRecoveryRunner:
    """UI-independent sequential phase scan using one downstream BPM SUM per RF."""

    def __init__(
        self,
        *,
        context,
        candidates: tuple[TransmissionRecoveryStage, ...],
        stages: tuple[TransmissionRecoveryStage, ...],
        settings: TransmissionRecoverySettings,
        directory: Path,
        callbacks: TransmissionRecoveryCallbacks | None = None,
        configuration_snapshot: Mapping[str, Any] | None = None,
        read: Callable[[str], Any] = caget,
        write: Callable[..., Any] = caput,
        lock_factory=EnergyControlLock.for_machine,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.context = context
        self.candidates = tuple(candidates)
        self.stages = tuple(stages)
        self.settings = settings
        self.directory = Path(directory)
        self.callbacks = callbacks or TransmissionRecoveryCallbacks()
        self.configuration_snapshot = (
            dict(configuration_snapshot)
            if configuration_snapshot is not None else None
        )
        self.read = read
        self.write = write
        self.lock_factory = lock_factory
        self.sleeper = sleeper
        self.stopping = False
        self.status = "pending"
        self.error = ""
        self.initial_snapshot: dict[str, Any] | None = None
        self.expected_phases: dict[str, float] = {}
        self.records = [
            {
                "rf_element": stage.rf_element,
                "target_bpm": stage.target_bpm,
                "status": "pending",
                "points": [],
            }
            for stage in self.stages
        ]

    def stop(self) -> None:
        self.stopping = True

    @staticmethod
    def _finite(value: Any, label: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label} is unavailable.") from exc
        if not math.isfinite(number):
            raise RuntimeError(f"{label} is not finite.")
        return number

    @staticmethod
    def _optional_finite(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _wait(self, duration_s: float, *, cancellable: bool = True) -> None:
        deadline = time.monotonic() + max(float(duration_s), 0.0)
        while True:
            if cancellable and self.stopping:
                raise TransmissionRecoveryCancelled(
                    "Transmission Recovery stopped by operator."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.sleeper(min(remaining, 0.05))

    def _set_phase(self, stage: TransmissionRecoveryStage, value: float) -> float:
        command = wrap_phase_deg(float(value))
        if not self.write(stage.phase_pv, command, wait=True, timeout=5):
            raise RuntimeError(f"{stage.rf_element} phase setpoint write failed.")
        wait_for_phase_readback(
            lambda: self.read(stage.phase_readback_pv),
            command,
            tolerance_deg=self.settings.readback_tolerance_deg,
            timeout_s=self.settings.readback_timeout_s,
            poll_interval_s=self.settings.readback_poll_interval_s,
        )
        return command

    def _validate_stages(self) -> None:
        self.settings.validate()
        if not self.stages:
            raise RuntimeError("No Transmission Recovery stages were selected.")
        candidate_indices = {stage.rf_element: stage.index for stage in self.candidates}
        selected = [stage.rf_element for stage in self.stages]
        if len(selected) != len(set(selected)):
            raise RuntimeError("Transmission Recovery contains duplicate RF stages.")
        if any(element not in candidate_indices for element in selected):
            raise RuntimeError("Transmission Recovery contains an unknown RF stage.")
        indices = [candidate_indices[element] for element in selected]
        if indices != sorted(indices):
            raise RuntimeError(
                "Selected Transmission Recovery stages must follow configured order."
            )

    def _snapshot(self) -> None:
        devices: dict[str, Any] = {}
        expected: dict[str, float] = {}
        for index, stage in enumerate(self.stages):
            setpoint = self._finite(
                self.read(stage.phase_pv), f"{stage.rf_element} phase setpoint"
            )
            readback = self._finite(
                self.read(stage.phase_readback_pv),
                f"{stage.rf_element} phase readback",
            )
            if (
                abs(phase_difference_deg(readback, setpoint))
                > self.settings.readback_tolerance_deg
            ):
                raise RuntimeError(
                    f"{stage.rf_element} phase readback does not match its setpoint."
                )
            devices[stage.rf_element] = {
                "phase_setpoint_deg": setpoint,
                "phase_readback_deg": readback,
                "phase_pv": stage.phase_pv,
                "phase_readback_pv": stage.phase_readback_pv,
                "target_bpm": stage.target_bpm,
                "signal_pv": stage.signal_pv,
            }
            expected[stage.rf_element] = setpoint
            self.records[index]["initial_phase_deg"] = setpoint
            self.callbacks.item(index, dict(self.records[index]))
        self.expected_phases = expected
        self.initial_snapshot = {
            "schema_version": "rf_transmission_recovery_snapshot_v1",
            "created_at": datetime.now().astimezone().isoformat(
                timespec="milliseconds"
            ),
            "machine_id": self.context.machine.id,
            "backend": self.context.control_backend.name,
            "devices": devices,
        }

    def _check_expected_state(self) -> None:
        for stage in self.stages:
            expected = self.expected_phases[stage.rf_element]
            setpoint = self._finite(
                self.read(stage.phase_pv), f"{stage.rf_element} phase setpoint"
            )
            readback = self._finite(
                self.read(stage.phase_readback_pv),
                f"{stage.rf_element} phase readback",
            )
            if (
                abs(phase_difference_deg(setpoint, expected))
                > self.settings.readback_tolerance_deg
            ):
                raise RuntimeError(
                    f"{stage.rf_element} phase changed outside this recovery run."
                )
            if (
                abs(phase_difference_deg(readback, expected))
                > self.settings.readback_tolerance_deg
            ):
                raise RuntimeError(
                    f"{stage.rf_element} phase readback drifted outside this recovery run."
                )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": "rf_transmission_recovery_v1",
            "machine_id": self.context.machine.id,
            "backend": self.context.control_backend.name,
            "status": self.status,
            "error": self.error,
            "updated_at": datetime.now().astimezone().isoformat(
                timespec="milliseconds"
            ),
            "configuration": {
                "candidate_stages": [
                    {
                        "rf_element": stage.rf_element,
                        "target_bpm": stage.target_bpm,
                    }
                    for stage in self.candidates
                ],
                "selected_stages": [
                    {
                        "rf_element": stage.rf_element,
                        "target_bpm": stage.target_bpm,
                    }
                    for stage in self.stages
                ],
                "settings": asdict(self.settings),
                "workflow_snapshot": self.configuration_snapshot,
            },
            "initial_snapshot": self.initial_snapshot,
            "expected_phases_deg": dict(self.expected_phases),
            "items": self.records,
            "directory": str(self.directory),
            "json_path": str(self.directory / "run.json"),
        }

    def _save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "run.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _update(self, index: int, **values: Any) -> None:
        self.records[index].update(values)
        self.callbacks.item(index, dict(self.records[index]))
        self._save()

    def _acquire_point(
        self,
        index: int,
        stage: TransmissionRecoveryStage,
        initial_phase: float,
        offset_deg: float,
        acquisition_index: int,
    ) -> dict[str, Any]:
        command = self._set_phase(stage, initial_phase + offset_deg)
        self._wait(self.settings.settle_time_s)
        samples: list[float | None] = []
        for sample_index in range(self.settings.samples_per_point):
            if self.stopping:
                raise TransmissionRecoveryCancelled(
                    "Transmission Recovery stopped by operator."
                )
            samples.append(self._optional_finite(self.read(stage.signal_pv)))
            if sample_index + 1 < self.settings.samples_per_point:
                self._wait(self.settings.sample_interval_s)
        valid = [sample for sample in samples if sample is not None]
        median_sum = (
            float(statistics.median(valid))
            if len(valid) >= self.settings.min_valid_samples
            else None
        )
        point = {
            "acquisition_index": acquisition_index,
            "offset_deg": float(offset_deg),
            "requested_phase_unwrapped_deg": float(initial_phase + offset_deg),
            "command_phase_deg": command,
            "samples": samples,
            "valid_samples": len(valid),
            "median_sum": median_sum,
            "status": "valid" if median_sum is not None else "invalid",
        }
        self.records[index]["points"].append(point)
        self.callbacks.point(index, dict(point))
        self._save()
        return point

    def _scan_stage(
        self,
        index: int,
        stage: TransmissionRecoveryStage,
        initial_phase: float,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        points: list[dict[str, Any]] = []
        scan_error: Exception | None = None
        try:
            for acquisition_index, offset in enumerate(
                self.settings.execution_offsets()
            ):
                if acquisition_index == (self.settings.points + 1) // 2:
                    self.callbacks.state(
                        f"{stage.rf_element}: returning to initial phase"
                    )
                    self._set_phase(stage, initial_phase)
                    self._wait(self.settings.settle_time_s)
                self.callbacks.state(
                    f"{stage.rf_element}: point {acquisition_index + 1}/"
                    f"{self.settings.points}"
                )
                points.append(
                    self._acquire_point(
                        index,
                        stage,
                        initial_phase,
                        offset,
                        acquisition_index,
                    )
                )
        except Exception as exc:
            scan_error = exc
        try:
            self._set_phase(stage, initial_phase)
            self._wait(self.settings.settle_time_s, cancellable=False)
        except Exception as restore_exc:
            if scan_error is not None:
                raise RuntimeError(
                    f"{scan_error}; phase restore failed: {restore_exc}"
                ) from restore_exc
            raise RuntimeError(
                f"{stage.rf_element} phase restore failed: {restore_exc}"
            ) from restore_exc
        if scan_error is not None:
            raise scan_error
        if self.stopping:
            raise TransmissionRecoveryCancelled(
                "Transmission Recovery stopped by operator."
            )

        valid = [point for point in points if point["median_sum"] is not None]
        if len(valid) < 2:
            raise RuntimeError(
                f"{stage.target_bpm} did not provide enough valid scan points."
            )
        values = {float(point["median_sum"]) for point in valid}
        if len(values) == 1:
            raise RuntimeError(
                f"{stage.target_bpm} SUM has no measurable phase response."
            )
        peak_sum = max(values)
        peaks = [
            point for point in valid if float(point["median_sum"]) == peak_sum
        ]
        best = min(
            peaks,
            key=lambda point: (
                abs(float(point["offset_deg"])),
                float(point["offset_deg"]),
            ),
        )
        return best, points

    def run(self) -> dict[str, Any]:
        active_index = 0
        lock = self.lock_factory(
            self.context.machine.id,
            {"app": "rf_phase_scan", "operation": "RF Transmission Recovery"},
        )
        try:
            self.status = "preflight"
            self._validate_stages()
            require_workflow_write_allowed(
                self.context, "rf_phase_scan", "RF Transmission Recovery"
            )
            lock.acquire()
            self._snapshot()
            self._save()
            self.status = "running"
            self._save()
            for active_index, stage in enumerate(self.stages):
                if self.stopping:
                    self.status = "stopped"
                    break
                self._check_expected_state()
                initial_phase = self.expected_phases[stage.rf_element]
                self._update(active_index, status="scanning", points=[])
                best, _points = self._scan_stage(
                    active_index, stage, initial_phase
                )
                best_phase = wrap_phase_deg(
                    float(best["requested_phase_unwrapped_deg"])
                )
                self.records[active_index].update(
                    peak_phase_deg=best_phase,
                    peak_offset_deg=float(best["offset_deg"]),
                    peak_sum=float(best["median_sum"]),
                )
                if self.stopping:
                    raise TransmissionRecoveryCancelled(
                        "Transmission Recovery stopped by operator."
                    )
                self._update(active_index, status="applying")
                try:
                    applied = self._set_phase(stage, best_phase)
                except Exception as apply_exc:
                    try:
                        self._set_phase(stage, initial_phase)
                    except Exception as restore_exc:
                        raise RuntimeError(
                            f"{apply_exc}; phase restore failed: {restore_exc}"
                        ) from restore_exc
                    raise
                if self.stopping:
                    try:
                        self._set_phase(stage, initial_phase)
                    except Exception as restore_exc:
                        raise RuntimeError(
                            f"{stage.rf_element} phase restore failed: "
                            f"{restore_exc}"
                        ) from restore_exc
                    raise TransmissionRecoveryCancelled(
                        "Transmission Recovery stopped by operator."
                    )
                self.expected_phases[stage.rf_element] = applied
                self._update(
                    active_index,
                    status="applied",
                    applied_phase_deg=applied,
                )
            else:
                self.status = "completed"
        except TransmissionRecoveryCancelled as exc:
            self.status = "stopped"
            self.error = str(exc)
            if self.records and self.records[active_index]["status"] in {
                "pending",
                "scanning",
                "applying",
            }:
                self.records[active_index].update(
                    status="stopped", error=self.error
                )
                self.callbacks.item(
                    active_index, dict(self.records[active_index])
                )
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            if self.records and self.records[active_index]["status"] in {
                "pending",
                "scanning",
                "applying",
            }:
                self.records[active_index].update(
                    status="failed", error=self.error
                )
                self.callbacks.item(
                    active_index, dict(self.records[active_index])
                )
        finally:
            for index, record in enumerate(self.records):
                if record["status"] == "pending":
                    record["status"] = "not_run"
                    self.callbacks.item(index, dict(record))
            try:
                self._save()
            finally:
                lock.release()
        return self._payload()


def restore_recovery_snapshot(
    *,
    context,
    stages: tuple[TransmissionRecoveryStage, ...],
    settings: TransmissionRecoverySettings,
    snapshot: Mapping[str, Any],
    expected_phases: Mapping[str, float],
    read: Callable[[str], Any] = caget,
    write: Callable[..., Any] = caput,
    lock_factory=EnergyControlLock.for_machine,
) -> dict[str, float]:
    """Restore one run snapshot after confirming no selected phase changed externally."""

    require_workflow_write_allowed(
        context, "rf_phase_scan", "RF Transmission Recovery Snapshot Restore"
    )
    devices = snapshot.get("devices")
    if not isinstance(devices, Mapping):
        raise RuntimeError("Transmission Recovery snapshot is invalid.")
    lock = lock_factory(
        context.machine.id,
        {"app": "rf_phase_scan", "operation": "RF Recovery Snapshot Restore"},
    )
    lock.acquire()
    try:
        for stage in stages:
            expected = float(expected_phases[stage.rf_element])
            setpoint = TransmissionRecoveryRunner._finite(
                read(stage.phase_pv), f"{stage.rf_element} phase setpoint"
            )
            readback = TransmissionRecoveryRunner._finite(
                read(stage.phase_readback_pv),
                f"{stage.rf_element} phase readback",
            )
            if (
                abs(phase_difference_deg(setpoint, expected))
                > settings.readback_tolerance_deg
                or abs(phase_difference_deg(readback, expected))
                > settings.readback_tolerance_deg
            ):
                raise RuntimeError(
                    f"{stage.rf_element} changed outside this recovery run; "
                    "snapshot restore was refused."
                )

        restored: dict[str, float] = {}
        for stage in stages:
            device = devices.get(stage.rf_element)
            if not isinstance(device, Mapping):
                raise RuntimeError(
                    f"Snapshot does not contain {stage.rf_element}."
                )
            initial = float(device["phase_setpoint_deg"])
            command = wrap_phase_deg(initial)
            if not write(stage.phase_pv, command, wait=True, timeout=5):
                raise RuntimeError(
                    f"{stage.rf_element} snapshot restore write failed."
                )
            wait_for_phase_readback(
                lambda pv=stage.phase_readback_pv: read(pv),
                command,
                tolerance_deg=settings.readback_tolerance_deg,
                timeout_s=settings.readback_timeout_s,
                poll_interval_s=settings.readback_poll_interval_s,
            )
            restored[stage.rf_element] = command
        return restored
    finally:
        lock.release()

