from __future__ import annotations

import json
import math
import statistics
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from epics import caget, caput

from half_linac.src.apps.rf_phase_scan.amp_scan import EpicsAmpClient
from half_linac.src.apps.rf_phase_scan.joint_phase_scan import (
    JointPhaseScanConfig,
    PhaseResponseMeasurement,
    select_joint_phase,
)
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
        response_routes: Mapping[str, JointPhaseScanConfig] | None = None,
        response_client_factory=EpicsAmpClient,
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
        self.response_routes = dict(response_routes) if response_routes is not None else None
        self.response_client_factory = response_client_factory
        self.stopping = False
        self.status = "pending"
        self.error = ""
        self.initial_snapshot: dict[str, Any] | None = None
        self.expected_phases: dict[str, float] = {}
        self.phase_commands: dict[str, tuple[float, ...]] = {}
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

    def _check_cancelled(self) -> None:
        if self.stopping:
            raise TransmissionRecoveryCancelled("Transmission Recovery stopped by operator.")

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
            if cancellable:
                self._check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.sleeper(min(remaining, 0.05))

    def _set_phase(self, stage: TransmissionRecoveryStage, value: float) -> float:
        command = wrap_phase_deg(float(value))
        if self.response_routes is not None:
            actual = self._finite(self.read(stage.phase_pv), f"{stage.rf_element} phase setpoint")
            if all(
                abs(phase_difference_deg(actual, expected)) > self.settings.readback_tolerance_deg
                for expected in self.phase_commands[stage.rf_element]
            ):
                raise RuntimeError(f"{stage.rf_element} phase setpoint changed outside this scan.")
            self.phase_commands[stage.rf_element] = (actual, command)
        if not self.write(stage.phase_pv, command, wait=True, timeout=5):
            raise RuntimeError(f"{stage.rf_element} phase setpoint write failed.")
        if self.response_routes is not None:
            self.phase_commands[stage.rf_element] = (command,)
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
        if self.response_routes is not None:
            for stage in self.stages:
                route = self.response_routes.get(stage.rf_element)
                if route is None or route.response.rf_element != stage.rf_element:
                    raise RuntimeError(f"{stage.rf_element} has no phase-response path.")

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
        self.phase_commands = {name: (phase,) for name, phase in expected.items()}
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
        payload = {
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
        if self.response_routes is not None:
            payload["configuration"].update(
                selection_mode="acceleration_and_sum",
                phase_response_routes={
                    name: asdict(route) for name, route in self.response_routes.items()
                },
            )
        return payload

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
        response: PhaseResponseMeasurement | None,
    ) -> dict[str, Any]:
        self._check_cancelled()
        command = self._set_phase(stage, initial_phase + offset_deg)
        self._wait(self.settings.settle_time_s)
        samples: list[float | None] = []
        for sample_index in range(self.settings.samples_per_point):
            self._check_cancelled()
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
        if response is not None:
            self._save()
            point["response"] = {}
            if median_sum is None or median_sum < response.route.minimum_transmission_sum:
                point["response"].update(
                    status="skipped", reason=f"{stage.target_bpm}: insufficient transmission SUM.",
                )
            else:
                response.measure(command, point["response"])
        self.callbacks.point(index, dict(point))
        self._save()
        return point

    def _scan_stage(
        self,
        index: int,
        stage: TransmissionRecoveryStage,
        initial_phase: float,
        response: PhaseResponseMeasurement | None,
    ) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        scan_error: Exception | None = None
        try:
            for acquisition_index, offset in enumerate(
                self.settings.execution_offsets()
            ):
                self._check_cancelled()
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
                        response,
                    )
                )
            if response is not None:
                self._repeat_reference(index, stage, points, response)
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
        self._check_cancelled()
        return points

    def _repeat_reference(self, index, stage, points, response):
        valid = [point for point in points if point["response"]["status"] == "valid"]
        if len(valid) < 3:
            return
        self._check_cancelled()
        reference = valid[0]
        self.callbacks.state(f"{stage.rf_element}: repeating reference response")
        phase = self._set_phase(stage, reference["command_phase_deg"])
        self._wait(self.settings.settle_time_s)
        repeat = {"acquisition_index": reference["acquisition_index"], "command_phase_deg": phase}
        self.records[index]["response_reference_repeat"] = repeat
        response.measure(phase, repeat)
        self._save()

    @staticmethod
    def _sum_peak(stage, points, *, require_variation):
        valid = [point for point in points if point["median_sum"] is not None]
        if len(valid) < (2 if require_variation else 1):
            raise RuntimeError(
                f"{stage.target_bpm} did not provide enough valid scan points."
            )
        values = {float(point["median_sum"]) for point in valid}
        if require_variation and len(values) == 1:
            raise RuntimeError(
                f"{stage.target_bpm} SUM has no measurable phase response."
            )
        peak_sum = max(values)
        peaks = [
            point for point in valid if float(point["median_sum"]) == peak_sum
        ]
        return min(
            peaks,
            key=lambda point: (
                abs(float(point["offset_deg"])),
                float(point["offset_deg"]),
            ),
        )

    def _choose_phase(self, index, stage, points, response):
        record = self.records[index]
        if response is not None and not any(point["median_sum"] is not None for point in points):
            for point in points:
                point["acceleration"] = "undetermined"
            record["initial_phase_acceleration"] = "undetermined"
            record["acceleration_analysis"] = {
                "status": "undetermined", "reason": f"{stage.target_bpm}: no usable transmission SUM.",
            }
            return None
        peak = self._sum_peak(stage, points, require_variation=response is None)
        record.update(
            peak_phase_deg=peak["command_phase_deg"],
            peak_offset_deg=peak["offset_deg"],
            peak_sum=peak["median_sum"],
        )
        if response is None:
            return peak
        repeat = record.get("response_reference_repeat", {})
        reference = points[repeat["acquisition_index"]] if repeat else None
        best, analysis = select_joint_phase(points, reference, repeat, response.route)
        record["acceleration_analysis"] = analysis
        record["initial_phase_acceleration"] = points[0]["acceleration"]
        if best is not None:
            record.update(
                selected_phase_deg=best["command_phase_deg"],
                selected_acquisition_index=best["acquisition_index"],
                selected_sum=best["median_sum"],
                selected_response=best["response"]["slope"],
            )
        return best

    def _run_stage(self, index, stage, initial_phase, response):
        self._check_cancelled()
        record = self.records[index]
        with response.open() if response is not None else nullcontext() as snapshot:
            if response is not None:
                record["response_snapshot"] = snapshot
            points = self._scan_stage(index, stage, initial_phase, response)
            best = self._choose_phase(index, stage, points, response)
            self._check_cancelled()
            if best is None:
                reason = record["acceleration_analysis"]["reason"]
                self._update(index, status="undetermined", error=reason)
                return False
            self._update(index, status="applying")
            if response is not None:
                self._check_expected_state()
                verification = record["response_application_verification"] = {"before": {}}
                response.verify_phase(initial_phase, verification["before"])
            try:
                applied = self._set_phase(stage, best["command_phase_deg"])
                if response is not None:
                    self._wait(self.settings.settle_time_s)
                    verification["after"] = {}
                    response.verify_phase(applied, verification["after"])
                self._check_cancelled()
            except Exception as exc:
                try:
                    self._set_phase(stage, initial_phase)
                except Exception as restore_exc:
                    raise RuntimeError(f"{exc}; phase restore failed: {restore_exc}") from restore_exc
                raise
            self.expected_phases[stage.rf_element] = applied
            self._update(index, status="applied", applied_phase_deg=applied)
            return True

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
                response = None
                if self.response_routes is not None:
                    response = PhaseResponseMeasurement(
                        context=self.context,
                        route=self.response_routes[stage.rf_element],
                        on_state=self.callbacks.state,
                        check_cancelled=self._check_cancelled,
                        client_factory=self.response_client_factory,
                        sleep=self.sleeper,
                    )
                if not self._run_stage(active_index, stage, initial_phase, response):
                    self.status = "undetermined"
                    self.error = self.records[active_index]["error"]
                    break
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

